"""Watch one key (F12) while N1MM Logger+ is the active window.

Uses a Windows low-level keyboard hook: Windows reports each key press
to PingPong and then passes it on unchanged, so N1MM still receives the
key. Only the chosen key is looked at, only while N1MMLogger.net.exe is
the foreground program, and nothing is recorded.

Note: if N1MM runs "as administrator" and PingPong does not, Windows
hides N1MM's key presses from PingPong - run both at the same level.
"""

import os
import sys
import threading

VK_F12 = 0x7B
N1MM_EXE = "n1mmlogger.net.exe"

# Keys blocked during an incoming call: only N1MM's function keys F1-F11
# (F12 stays free - it is the call key). Esc, Enter and all other keys
# are never blocked, so an operator can always stop a transmission.
SEND_KEYS = {vk: "F{}".format(vk - 0x6F) for vk in range(0x70, 0x7B)}
LLKHF_ALTDOWN = 0x20

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_TIMER = 0x0113
REHOOK_MS = 10000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def supported():
    return sys.platform == "win32"


_api = None


def _winapi():
    """Windows functions used per key press, prepared once (speed matters:
    Windows drops a keyboard hook that answers too slowly)."""
    global _api
    if _api is None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _api = (ctypes, wintypes, user32, kernel32, {})
    return _api


def foreground_exe():
    """Lower-case file name of the program owning the active window."""
    ctypes, wintypes, user32, kernel32, cache = _winapi()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in cache:
        return cache[pid.value]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        name = ""
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
        if len(cache) > 200:
            cache.clear()
        cache[pid.value] = name
        return name
    finally:
        kernel32.CloseHandle(handle)


class KeyWatcher:
    """Watches the keyboard while N1MM is the active window.

    on_press() is called when vk (F12) goes down. When should_block()
    returns True, the SEND_KEYS are swallowed (N1MM never gets them) and
    on_blocked(name) is called. Alt combinations are never touched.
    """

    def __init__(
        self, on_press, vk=VK_F12, only_exe=N1MM_EXE,
        should_block=None, on_blocked=None, on_event=None,
    ):
        self.on_press = on_press
        self.on_event = on_event or (lambda text: None)
        self.should_block = should_block
        self.on_blocked = on_blocked
        self.vk = vk
        self.only_exe = only_exe
        self.error = None
        self._thread = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._down = False
        self._proc = None

    def start(self):
        """Install the hook; returns True on success (else see .error)."""
        if not supported():
            self.error = "only available on Windows"
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self.error is None and self._thread_id != 0

    def stop(self):
        if self._thread_id:
            import ctypes

            ctypes.WinDLL("user32").PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = 0

    def decide(self, vk, flags, wparam, foreground=None):
        """What to do with one key event: "block", "press" or None.

        Called for every key; must be quick. foreground() is only asked
        for the keys we care about (default: real foreground program).
        """
        fg = foreground or foreground_exe
        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if (
            vk in SEND_KEYS
            and not (flags & LLKHF_ALTDOWN)
            and self.should_block is not None
            and self.should_block()
            and (not self.only_exe or fg() == self.only_exe)
        ):
            # F10 alone arrives as WM_SYSKEYDOWN, Alt is not down then.
            if down and self.on_blocked:
                self.on_blocked(SEND_KEYS[vk])
            return "block"
        if vk == self.vk:
            if down:
                if not self._down:  # ignore auto-repeat
                    self._down = True
                    name = fg()
                    if not self.only_exe or name == self.only_exe:
                        self.on_press()
                        return "press"
                    self.on_event("F12 ignored - active program: {}".format(name or "?"))
            elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                self._down = False
        return None

    def _run(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            HOOKPROC,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        errors = [0]

        def proc(code, wparam, lparam):
            if code == 0:
                try:
                    kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if self.decide(kb.vkCode, kb.flags, wparam) == "block":
                        return 1  # swallowed: N1MM never sees this key
                except Exception as exc:
                    errors[0] += 1
                    if errors[0] <= 5:
                        self.on_event("key handler error: {!r}".format(exc))
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(proc)  # keep a reference while hooked
        _winapi()  # prepare the per-key calls before the first key
        module = kernel32.GetModuleHandleW(None)

        def install():
            return user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)

        hook = install()
        if not hook:
            self.error = "keyboard hook failed (error {})".format(ctypes.get_last_error())
            self._ready.set()
            return
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        self.on_event("keyboard watcher installed")
        # Windows silently drops a low-level hook that once answers too
        # slowly, and a hook installed later by another program sees keys
        # first. Re-installing every REHOOK_MS fixes both.
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        user32.SetTimer(None, 0, REHOOK_MS, None)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                fresh = install()
                if fresh:
                    user32.UnhookWindowsHookEx(hook)
                    hook = fresh
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook)
