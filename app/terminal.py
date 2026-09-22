"""PTY Pseudo-Terminal and RFC 6455 WebSocket Engine for Antigravity Remote.

Provides authenticated, low-latency, full-duplex terminal sessions to mobile
and web browsers using Linux native PTY and standard library networking only.
Pure Python standard library only.
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import select
import signal
import struct
import termios
import threading
import time

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def compute_accept_key(sec_ws_key: str) -> str:
    """Compute RFC 6455 Sec-WebSocket-Accept key for handshake upgrade."""
    combined = sec_ws_key.strip() + WS_GUID
    digest = hashlib.sha1(combined.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def encode_ws_frame(payload: bytes, opcode: int = 1) -> bytes:
    """Encode an unmasked RFC 6455 frame from server to client."""
    length = len(payload)
    if length < 126:
        header = bytearray([0x80 | (opcode & 0x0F), length])
    elif length < 65536:
        header = bytearray([0x80 | (opcode & 0x0F), 126]) + struct.pack("!H", length)
    else:
        header = bytearray([0x80 | (opcode & 0x0F), 127]) + struct.pack("!Q", length)
    return bytes(header) + payload


def read_exact(sock, n: int) -> bytes:
    """Read exactly n bytes from a socket, or return empty bytes on EOF."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return bytes()
        buf.extend(chunk)
    return bytes(buf)


def decode_ws_frame(sock):
    """Decode a masked RFC 6455 frame sent from client to server.

    Returns:
        (fin, opcode, payload) or (None, None, None) on disconnect or protocol error.
    """
    header = read_exact(sock, 2)
    if len(header) < 2:
        return None, None, None

    b1, b2 = header[0], header[1]
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    has_mask = bool(b2 & 0x80)
    payload_len = b2 & 0x7F

    if payload_len == 126:
        ext = read_exact(sock, 2)
        if len(ext) < 2:
            return None, None, None
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = read_exact(sock, 8)
        if len(ext) < 8:
            return None, None, None
        payload_len = struct.unpack("!Q", ext)[0]

    # Security ceiling: reject single frame larger than 4MB
    if payload_len > 4 * 1024 * 1024:
        return None, None, None

    mask = read_exact(sock, 4) if has_mask else None
    if has_mask and len(mask) < 4:
        return None, None, None

    payload = read_exact(sock, payload_len)
    if len(payload) < payload_len:
        return None, None, None

    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return fin, opcode, payload


def resize_pty(master_fd: int, rows: int, cols: int):
    """Update pseudo-terminal window dimensions via TIOCSWINSZ ioctl."""
    rows = max(2, min(rows, 300))
    cols = max(10, min(cols, 500))
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass


def spawn_shell_pty(rows: int = 24, cols: int = 80):
    """Allocate pseudo-terminal pair and spawn interactive user login shell.

    Returns:
        (master_fd, child_pid)
    """
    master_fd, slave_fd = pty.openpty()
    resize_pty(master_fd, rows, cols)

    pid = os.fork()
    if pid == 0:
        # Child process: configure terminal slave and exec shell
        os.close(master_fd)
        os.setsid()

        # Set controlling terminal
        try:
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        except Exception:
            pass

        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

        # Standard interactive environment
        home = os.path.expanduser("~")
        shell = os.environ.get("SHELL", "/bin/bash")
        if not os.path.exists(shell):
            shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"

        os.environ["HOME"] = home
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        os.environ["LANG"] = "en_US.UTF-8"
        os.environ["LC_ALL"] = "en_US.UTF-8"

        try:
            os.chdir(home)
        except OSError:
            pass

        os.execlp(shell, shell, "-l")
        os._exit(1)

    # Parent process
    os.close(slave_fd)
    return master_fd, pid


class TerminalSession:
    """Manages full-duplex bridge between upgraded WebSocket socket and PTY master."""

    def __init__(self, sock, rows: int = 24, cols: int = 80):
        self.sock = sock
        self.rows = rows
        self.cols = cols
        self.master_fd = None
        self.child_pid = None
        self.alive = True
        self.lock = threading.Lock()

    def start(self):
        """Boot PTY process and start bidirectional communication loop."""
        try:
            self.master_fd, self.child_pid = spawn_shell_pty(self.rows, self.cols)
        except Exception as e:
            self.close()
            return

        # Start background thread to pump PTY output -> WebSocket client
        reader_thread = threading.Thread(target=self._pty_to_ws_pump, daemon=True)
        reader_thread.start()

        # Main thread pumps WebSocket client -> PTY input
        self._ws_to_pty_pump()

    def _pty_to_ws_pump(self):
        """Read output from master PTY file descriptor and stream to WebSocket."""
        try:
            while self.alive:
                fd = self.master_fd
                if fd is None:
                    break
                try:
                    r, _, _ = select.select([fd], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if not r or not self.alive:
                    continue
                try:
                    fd = self.master_fd
                    if fd is None:
                        break
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    frame = encode_ws_frame(data, opcode=1)  # UTF-8 text frame
                    with self.lock:
                        if self.alive:
                            self.sock.sendall(frame)
                except (OSError, BrokenPipeError, TypeError):
                    break
        finally:
            self.close()

    def _ws_to_pty_pump(self):
        """Read incoming WebSocket frames and route keystrokes / control payloads to PTY."""
        try:
            while self.alive:
                fin, opcode, payload = decode_ws_frame(self.sock)
                if opcode is None or opcode == 8:  # Connection Close
                    break
                if opcode == 9:  # Ping -> reply Pong
                    with self.lock:
                        if self.alive:
                            self.sock.sendall(encode_ws_frame(payload, opcode=10))
                    continue
                if opcode == 10:  # Pong
                    continue

                if opcode in (1, 2):  # Text or Binary
                    # Check for JSON control packet (e.g. resize)
                    if payload.startswith(b'{"') and b'"type"' in payload:
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            if ctrl.get("type") == "resize":
                                r = int(ctrl.get("rows", 24))
                                c = int(ctrl.get("cols", 80))
                                if self.master_fd is not None:
                                    resize_pty(self.master_fd, r, c)
                                continue
                        except Exception:
                            pass

                    # Raw terminal keystrokes / input
                    if self.master_fd is not None:
                        try:
                            os.write(self.master_fd, payload)
                        except OSError:
                            break
        finally:
            self.close()

    def close(self):
        """Cleanly tear down PTY, terminate child shell process, and close socket."""
        with self.lock:
            if not self.alive:
                return
            self.alive = False

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self.child_pid is not None:
            try:
                os.kill(self.child_pid, signal.SIGHUP)
                time.sleep(0.05)
                os.kill(self.child_pid, signal.SIGTERM)
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    pid, _ = os.waitpid(self.child_pid, os.WNOHANG)
                    if pid != 0:
                        break
                    time.sleep(0.05)
                else:
                    os.kill(self.child_pid, signal.SIGKILL)
                    os.waitpid(self.child_pid, 0)
            except OSError:
                pass
            self.child_pid = None

        try:
            self.sock.close()
        except Exception:
            pass
