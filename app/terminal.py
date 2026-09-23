"""PTY Pseudo-Terminal and RFC 6455 WebSocket Engine for Antigravity Remote.

Provides authenticated, persistent, low-latency, full-duplex terminal sessions
to mobile and web browsers using Linux native PTY, ring buffer replay, and standard
library networking only. Pure Python standard library only.
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import secrets
import select
import signal
import struct
import termios
import threading
import time

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
RING_BUFFER_CAPACITY = 131072  # 128KB output replay backlog per persistent session
IDLE_SESSION_TTL = 7200  # 2 hours session survival after client disconnect


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
    """Read exactly n bytes from a socket, or return empty bytes on EOF or reset."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (ConnectionResetError, BrokenPipeError, OSError):
            return bytes()
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


def read_ws_message(sock):
    """Read an assembled RFC 6455 message, properly joining continuation frames (opcode 0).

    Returns:
        (opcode, payload) or (None, None) on disconnect / protocol error.
    """
    fragments = []
    first_opcode = None
    while True:
        fin, opcode, payload = decode_ws_frame(sock)
        if opcode is None:
            return None, None
        # RFC 6455: Control frames (8=Close, 9=Ping, 10=Pong) cannot be fragmented
        if opcode in (8, 9, 10):
            return opcode, payload
        if first_opcode is None:
            first_opcode = opcode
        fragments.append(payload)
        if fin:
            break
    return first_opcode, b"".join(fragments)


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

    # Enable IUTF8 flag on slave PTY line discipline (fixes multibyte Backspace in canonical mode)
    try:
        attrs = termios.tcgetattr(slave_fd)
        iutf8_flag = getattr(termios, "IUTF8", 0o040000)
        attrs[0] |= iutf8_flag
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
    except Exception:
        pass

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

        # Reset signal handlers to default and unmask signals (reverses Python's inherited SIGPIPE = SIG_IGN)
        for s in (signal.SIGPIPE, signal.SIGXFSZ, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(s, signal.SIG_DFL)
            except (ValueError, OSError):
                pass
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, [])
        except AttributeError:
            pass

        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)

        # Build clean environment without mutating parent process memory
        home = os.path.expanduser("~")
        shell = os.environ.get("SHELL", "/bin/bash")
        if not os.path.exists(shell):
            shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"

        env = dict(os.environ)
        env["HOME"] = home
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        # Preserve existing UTF-8 locale if present, else fallback cleanly to C.UTF-8
        current_lang = env.get("LANG", "")
        if "UTF-8" not in current_lang.upper() and "UTF8" not in current_lang.upper():
            env["LANG"] = "C.UTF-8"
        env.pop("LC_ALL", None)

        try:
            os.chdir(home)
        except OSError:
            pass

        os.execvpe(shell, [shell, "-l"], env)
        os._exit(1)

    # Parent process
    os.close(slave_fd)
    return master_fd, pid


class PTYProcess:
    """Manages independent, persistent PTY shell process and output ring buffer."""

    def __init__(self, session_id: str, rows: int = 24, cols: int = 80):
        self.session_id = session_id
        self.rows = rows
        self.cols = cols
        self.master_fd = None
        self.child_pid = None
        self.alive = True
        self.lock = threading.RLock()
        self.ring_buffer = bytearray()
        self.active_sock = None
        self.last_active = time.time()
        self._reader_thread = None

    def start(self):
        """Allocate PTY, spawn shell process, and start background reader pump."""
        self.master_fd, self.child_pid = spawn_shell_pty(self.rows, self.cols)
        self._reader_thread = threading.Thread(
            target=self._read_loop, name=f"pty-reader-{self.session_id[:8]}", daemon=True
        )
        self._reader_thread.start()

    def _read_loop(self):
        """Continuously read PTY output, append to replay buffer, and stream to active client."""
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

                # Coalesce burst output over a tiny 5ms window to reduce network frame thrashing
                burst = bytearray(data)
                while len(burst) < 32768:
                    try:
                        r_quick, _, _ = select.select([fd], [], [], 0.005)
                        if not r_quick:
                            break
                        extra = os.read(fd, 4096)
                        if not extra:
                            break
                        burst.extend(extra)
                    except (OSError, ValueError):
                        break

                chunk = bytes(burst)
                with self.lock:
                    self.last_active = time.time()
                    # Append to ring buffer, trimming oldest bytes if exceeding capacity
                    self.ring_buffer.extend(chunk)
                    if len(self.ring_buffer) > RING_BUFFER_CAPACITY:
                        excess = len(self.ring_buffer) - RING_BUFFER_CAPACITY
                        del self.ring_buffer[:excess]

                    sock = self.active_sock
                    if sock is not None:
                        try:
                            # Stream as RFC 6455 Binary Frame (opcode 2) - eliminates UTF-8 boundary 1007 drops
                            sock.sendall(encode_ws_frame(chunk, opcode=2))
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            self.active_sock = None
            except (OSError, BrokenPipeError):
                break

        self.close()

    def write(self, data: bytes):
        """Write raw keystrokes / input to master PTY."""
        with self.lock:
            self.last_active = time.time()
            fd = self.master_fd
        if fd is not None:
            try:
                os.write(fd, data)
            except OSError:
                pass

    def resize(self, rows: int, cols: int):
        """Resize terminal dimensions."""
        with self.lock:
            self.rows = rows
            self.cols = cols
            self.last_active = time.time()
            fd = self.master_fd
        if fd is not None:
            resize_pty(fd, rows, cols)

    def attach(self, sock) -> bytes:
        """Attach active WebSocket connection and return buffered output backlog for replay."""
        with self.lock:
            self.active_sock = sock
            self.last_active = time.time()
            return bytes(self.ring_buffer)

    def detach(self, sock=None):
        """Detach socket when client disconnects (preserves running PTY and shell!)."""
        with self.lock:
            if sock is None or self.active_sock == sock:
                self.active_sock = None
                self.last_active = time.time()

    def close(self):
        """Terminate process group and clean up file descriptors."""
        with self.lock:
            if not self.alive:
                return
            self.alive = False
            sock = self.active_sock
            self.active_sock = None
            fd = self.master_fd
            self.master_fd = None
            pid = self.child_pid
            self.child_pid = None

        if sock is not None:
            try:
                sock.sendall(encode_ws_frame(b"", opcode=8))
                sock.close()
            except Exception:
                pass

        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        if pid is not None:
            try:
                # Terminate entire process group spawned inside PTY session
                os.killpg(pid, signal.SIGHUP)
                time.sleep(0.02)
                os.killpg(pid, signal.SIGTERM)
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    p, _ = os.waitpid(pid, os.WNOHANG)
                    if p != 0:
                        break
                    time.sleep(0.03)
                else:
                    os.killpg(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
            except (ProcessLookupError, OSError):
                pass


class PTYSessionManager:
    """Registry managing decoupled, persistent PTY terminal sessions."""

    def __init__(self):
        self.sessions: dict[str, PTYProcess] = {}
        self.lock = threading.RLock()
        self._reaper_thread = threading.Thread(target=self._reaper_loop, daemon=True)
        self._reaper_thread.start()

    def get_or_create(self, session_id: str | None, rows: int = 24, cols: int = 80) -> tuple[PTYProcess, bool]:
        """Obtain existing active session or spawn a fresh one.

        Returns:
            (process, is_new)
        """
        with self.lock:
            if session_id and session_id in self.sessions:
                proc = self.sessions[session_id]
                if proc.alive:
                    return proc, False
                self.sessions.pop(session_id, None)

            new_id = session_id if (session_id and len(session_id) >= 8) else secrets.token_hex(16)
            proc = PTYProcess(new_id, rows, cols)
            proc.start()
            self.sessions[new_id] = proc
            return proc, True

    def close_session(self, session_id: str):
        """Explicitly terminate a single session."""
        with self.lock:
            proc = self.sessions.pop(session_id, None)
        if proc:
            proc.close()

    def close_all(self):
        """Terminate all sessions (used for admin killswitch)."""
        with self.lock:
            procs = list(self.sessions.values())
            self.sessions.clear()
        for p in procs:
            p.close()

    def _reaper_loop(self):
        """Periodically clean up dead sessions or sessions idle beyond timeout."""
        while True:
            time.sleep(30)
            now = time.time()
            to_close = []
            with self.lock:
                for sid, proc in list(self.sessions.items()):
                    if not proc.alive:
                        to_close.append(sid)
                    elif proc.active_sock is None and (now - proc.last_active > IDLE_SESSION_TTL):
                        to_close.append(sid)
                for sid in to_close:
                    self.sessions.pop(sid, None)


SESSION_MANAGER = PTYSessionManager()


def get_session_manager() -> PTYSessionManager:
    """Return global PTYSessionManager singleton."""
    return SESSION_MANAGER


def close_all_terminal_sessions():
    """Admin killswitch helper to terminate all terminal sessions."""
    SESSION_MANAGER.close_all()


class TerminalSession:
    """Bridges WebSocket client connection to a persistent PTYProcess."""

    def __init__(self, sock, rows: int = 24, cols: int = 80, session_id: str | None = None):
        self.sock = sock
        self.rows = rows
        self.cols = cols
        self.requested_session_id = session_id
        self.pty_proc: PTYProcess | None = None
        self.connected_at = time.time()

    def start(self):
        """Attach to persistent PTY session, replay backlog, and pump WebSocket frames."""
        try:
            self.pty_proc, is_new = SESSION_MANAGER.get_or_create(
                self.requested_session_id, self.rows, self.cols
            )
        except Exception:
            try:
                self.sock.close()
            except Exception:
                pass
            return

        # Attach socket and retrieve buffer backlog
        backlog = self.pty_proc.attach(self.sock)

        try:
            # 1. Send session identity frame as text JSON
            meta = json.dumps({
                "type": "session",
                "id": self.pty_proc.session_id,
                "reconnected": not is_new,
            }).encode("utf-8")
            self.sock.sendall(encode_ws_frame(meta, opcode=1))

            # 2. Replay terminal backlog as binary frame so client screen repaints
            if backlog:
                self.sock.sendall(encode_ws_frame(backlog, opcode=2))

            # 3. Enter WebSocket client -> PTY input pump
            self._ws_to_pty_pump()
        finally:
            # On disconnect: detach socket but leave shell process running in background!
            if self.pty_proc:
                self.pty_proc.detach(self.sock)
            try:
                self.sock.close()
            except Exception:
                pass

    def _ws_to_pty_pump(self):
        """Read incoming WebSocket frames and route to PTY or control dispatcher."""
        while self.pty_proc and self.pty_proc.alive:
            # Enforce 24-hour max connection duration security ceiling
            if time.time() - self.connected_at > 86400:
                try:
                    self.sock.sendall(encode_ws_frame(b"", opcode=8))
                except Exception:
                    pass
                break

            opcode, payload = read_ws_message(self.sock)
            if opcode is None or opcode == 8:  # Connection close or reset
                break
            if opcode == 9:  # Ping -> reply with Pong
                try:
                    self.sock.sendall(encode_ws_frame(payload, opcode=10))
                except Exception:
                    break
                continue
            if opcode == 10:  # Pong received
                continue

            if opcode == 1:  # Text frame -> control commands (resize, ping) or text input fallback
                handled_ctrl = False
                try:
                    ctrl = json.loads(payload.decode("utf-8", errors="replace"))
                    if isinstance(ctrl, dict):
                        msg_type = ctrl.get("type")
                        if msg_type == "ping":
                            self.sock.sendall(encode_ws_frame(b'{"type":"pong"}', opcode=1))
                            handled_ctrl = True
                        elif msg_type == "resize":
                            r = int(ctrl.get("rows", 24))
                            c = int(ctrl.get("cols", 80))
                            self.pty_proc.resize(r, c)
                            handled_ctrl = True
                except Exception:
                    pass
                if not handled_ctrl and self.pty_proc:
                    self.pty_proc.write(payload)
                continue

            if opcode == 2:  # Binary frame -> raw keystrokes / terminal input
                if self.pty_proc:
                    self.pty_proc.write(payload)
