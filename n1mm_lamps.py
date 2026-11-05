"""PingPong - N1MM Logger+ UDP 12060 station lamp.

One compact always-on-top window that shows which station is
currently transmitting. The lamp lights up in the station's color
exactly while RadioInfo reports IsTransmitting=true and shows the
caption of the key that started the transmission ("Fx" prefix removed).

Group: only stations on the same band and mode as this PC's N1MM are
shown (bands 1.8, 3.5, 7, 14, 21, 28 MHz; modes CW, RTTY, SSB with
USB/LSB counted as SSB), so separate radios never clash.

Lamps talk to each other with a small hello broadcast on UDP 46070
(never on N1MM's ports): each PC can choose its own color (unique in
the network), send an F12 call that makes S&P stations on the same
band blink and optionally blocks their F1-F11, and report its state
for Diagnostics. The Options menu also updates the exe from GitHub
releases and can switch back to an older version.

Stations are detected automatically; optional lamps.cfg
("IP,label,color" per line) gives fixed labels/colors. Per-PC settings
are kept in pingpong.ini next to the exe.

The program is split into parts:

- n1mm_lamps.py      - this file: the window, Options menu, updates
- pingpong_rules.py  - what the lamp decides (calls, lock, master, ...)
- pingpong_net.py    - messages and UDP loops (N1MM 12060, lamps 46070)
- pingpong_common.py - constants, N1MM packet parsing, station records
- pingpong_keys.py   - keyboard watcher (F12, F-key block, Shift+F2/F4)
- pingpong_update.py - self-update from GitHub releases
- pingpong_version.py - the version number

Made by S55OO with AI assistance.

Usage:
    python n1mm_lamps.py [--port 12060] [--stale 5.0] [--config lamps.cfg]
                         [--discovery-port 46070] [--no-discovery]
"""

import argparse
import base64
import os
import re
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import colorchooser, messagebox, simpledialog

import pingpong_keys as keys
import pingpong_update as upd
from pingpong_common import *  # noqa: F401,F403
from pingpong_common import _float, guarded, log_error  # noqa: F401
from pingpong_net import *  # noqa: F401,F403
from pingpong_net import _sender_socket  # noqa: F401
from pingpong_rules import RulesMixin
from pingpong_version import __version__

HELP_URL = "https://github.com/s55oo/N1MMPingPong"

HELP_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAABY0lEQVR4nGNgoDJgxCcplb/1PzbxZxO9cepjJMUgYgxmxGcYIyMDQ4ajEkOCjTyDBD8Hw6N33xhm7r/PsOTYI5yGMuFzWaKtAkOJpypD08brDDrVuxmm7rnL0BqizRBpIYtTDxNOCUZGhmIPVYaFRx4xbL34guHzjz8MK04+YVhx4jFYHJdDmHAED4OGFC+DABcrw77rr1DELz76yCApwMHAw86CVR8TNteBwLWnnxikC7YxHLn1FkVcS5qP4dP33wzffv3F6komBhKArZoIQ6yVHMPsgw8Y/v3HnhCYiDXMWEGQYX6KMcP+G68ZJu66g1MdEzGGMTMxMvRF6jJcefqJIX3BOYa///5TZqCONB+DijgPQ+umGww/f//Dq5aJGAPF+NjB9K2XXwiqZSQ1u+ECz6A5higXRlnIMjyd4MWgL8tPvAspceUzpPzMRGyxRIxhDNi8TIqhRBVfyICcApbqAAC2c3+GEqNjHgAAAABJRU5ErkJggg=="
)

APP_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAS/0lEQVR4nN2ba4xdV3XHf2ufc+5r"
    "xuOxxxPi4OBgk9jYsZ1nQ0hgpBDCowm0pUNVQFCqQqEUUakSog9UQYUEX/qhH1paEKpKKx4DFa+q"
    "DdgoiZMmIbETx9g4jmMSJ45t/BjPeGbu45y9Vz/sfR53ZmIbiENg2Vt3zr3n7r3Xf73XvgfOTTI2"
    "Nhafx30vKQp7lnPdd44bxiOYsPnVmlffcDnqrgRdDbLkl97lC0IOMChuBuUZp/z46f079hUfj49H"
    "TJQ8zKfnA0DCcK/YtGlZkjbej+i7VNlioiiWcwP7KyFFcdY6YDfIV+l1v3Tw4GM/AwygYfTRYpxI"
    "fuOadde9HyOfMia6VNWhzoGq1UUmeimQgCASiTEYMThrjzn00z/d9/A/hVsMXmWq35k/B7phw4Za"
    "R1tfMCZ+rzqLcy4TEUOpGS9lUkBV1RljYmMirLPfbMjsH+3du3eGeSBUmREYN6961SOxi4e/HUXx"
    "m7IszRk3LzITLxSpKlkcx0lms/uaZu7Ne/funaNiDiVj4+MGJqyLln4xipM32SzrGTGxIEb4tf0n"
    "RiSxWZbGUXJTxzX/E3CeV08mMB8xMWHXrv+t98Zx7T02zVIRqb2osrqAJAUItbetXXf9R5mYsIyP"
    "R1Cx6dWrtyyNW8k+ETOqOk87fjPICYLDzViRdU/vfegYIGZsbCwC1DTj90dR8jLnnA+sv3lknDoX"
    "R8lQrHwY0LGxMVNowNr11z1iomizc9aBRL/avV4wciJGnHM/XTbg1u/YsSMzgK5d+5q1iFzpnBOQ"
    "30Tp52RUHcbImum22QioAdAk22yiKELV8tKP878Uqao1JsKiVwPEAIJcZjA4RF+MNDcyBqeKMX4t"
    "5xQjgnXuHN98YUgQRKPLoARg8EVZOdD0mRmSJKGXpgDUkoQ0TRkYaL2Iu3BLIAAAcsFzexHBGEOv"
    "1+NDH3gPb7jlddy9/X4Axl53I9t+uJ1/+/IEtVoN5xwhFF+4/QSePQDVLP8CWICIoKocPnKUP3zn"
    "2/jExz/CmTMzbNxwOQDWWq6/bjPPPneEr3z9O1z8slHEyIUDocLjBW90iAjdbo/R0RG2bHo1V23Z"
    "SLvd4cyZWVqtBgBzcx3iOOaqLRs5deo0Tx06zPHjJ6nXaxdcEy4oACJClmWsWLGcf/7Hz7Bh/eWc"
    "PHWaY8eOM7J8GY8/cRCAdZev4dix49z+llt537t/n737nuBDH/1rJidPE8fxBQWhAKBaQrxQFJmI"
    "6bkZ7njrdWy6cj2HDh1m5cqL+Jcvfpfvfm8rk6enAFg2vJQ7br+VP/2Td3P4uaNsunI9N994HV/7"
    "xvdYNrwUa5+3ofMLUZXHC570DLSa7Nt/kJMnJ1n18pU4pzz08GPs2bufbrdHt9tjz979PPTwYzin"
    "rHr5Sk6enGTf/oMMtJoXenu/iAnoYi8LyBihPdem2+uyY+dj/PlffJJbb7mZ+x/cyQMP7mR0dATn"
    "vGRHR0d44MGdfORjf8uNN1zD1h/ey46dj5HEETaz1Bs1nFt8JXn+P86Lfg4ANP/f/94iZMTQbndY"
    "d8Va7vjtW9n5yG7+5867uP+BHSRJQqvV7FNray2NRp2773mArdu2o6rc/pZbuObqTXz3v7fy+P4n"
    "aTYbuEUSJb8DKbYiaMDg/IA4DwDmM14V/UIARIQ07TE6sozPfeYTvPKVr+Cd77iddrvLgw89QqvV"
    "JMsW2rSqsmTJAHNzbW64/mo++5m/ol6vcfNrr+MDH/44k6enzsshagDjfIEofYAsMlDfO5Kc2epw"
    "CBqGK4ZBsVnKyMgwI8uHOfzsEWq1mFWrLqbd6eBU/ayilbX8e06VdrfDqksvplaLOXz4CCtGhlkx"
    "Moy1KcYoRhxGHFIMRcSF+RTC31oohS7Om5kPwEKRlPJVDdgqooqpjEgdkWoxxFla9RoHnzjI1776"
    "LQywbdu9bN22ncGBFi5X/UKDSvVyzjI40GLrtu1s++G9RKJ89evf4smDBxlo1jBYIqNERomNEokf"
    "RtRXMl7+FSHlyzy/1gjAFRtu/GQUR5/2TVDi4n4tJxItAfAgKgvbxOqvRMico5dmrLholFOTU2TO"
    "ESdJabPS/5X8wgikaUocCSPLl3L8xAnqtZg4MqDeB4j4rfn2r+DUXzsVVEu9LFmUwhp8kzSJM5v9"
    "wxN7HvjLBT5gAfMF4/78JR8RXn380GIxRel2e9SSmHqjxukTJ4jjmCiOsM75tFj6BF/Zql+vUYsQ"
    "cZyePMnSgRqqGc72aNQTRMrtqYJVxTnBOrAOnCpOBae5mHJoS0dZpQCA5tMWMFWZN1oyHaPEQIQS"
    "CwUQIuCcw6nyytWXcGJyiqmZOQZrCamDTB3gpQWKy9kOyxnxMouMIzIQR0qtEeNsl+GlLUZXXMTR"
    "o0cxRjAigXnPdGb9SMOrdYArhVnyLcxHoU8DVEFkIfMRWjBeQ0kEauK/XIAgQi/LeONtY4yN3cTT"
    "zx3jC//+dU6dmSWKInoKGZChWCeIKCrBYxfMK3GkJJFSi4XYpCwfGeSDf/xOVl/6Mu6+5z5+sPVu"
    "6vUE6xSbM54pvYy+IeQaMQ+E0vdIBYBK+am5wvQzX0OpC9TBv4pQFw9AbpNDzTrXr1uLnpnmiotH"
    "WX/pSh7e/RNMEiFWC/y9xw9XwR0Y8Y6tFkM9hlZDcVnKhstXsm7NCrqdKW64Zg27dt6Pta5YM7XQ"
    "7UEnhU5PiXp+P4VbcV5Tcg1Q+n1iHO4JjcDc4VHYfM58Q6Ap0BBoGc+8IiQCgwJNI3TSjN0/2slV"
    "N1zL008+zeShZxhp1OnmoUi1cF5hXQAiUeIIkhgaidKsQ6sOzSU1Tp88xP59e7hk5UX8eNcOlg+m"
    "NOsJ7Z4yMwdRBo2a0ujBbASRKTWqkLULDrKq/iGpigEMBhHpC5OR5CoPDYSmgQGBQeOZrouwqS5s"
    "qgkXGQ+Mpcbkk3s4cfgpHplqY3uWpbFhNuihOp0foBDxm06iwHwNBhvCYBMGm4LNUh790fcZvrrO"
    "69fM8o6rIiLjaPfgyKSw60nYecBgREliMIbSUVZV3+WAi/9cTGkCqjZYch7efIiLgSSofKvC/MrY"
    "8PaWcFnksOoQMZiw6nAzYY1ts3nIcFfHcFfbISZXe78Jq+o3E8wnNlCLoJ5Asw4DDRhsQhIJb74u"
    "4rZrLLVoGiNR0bxpNmH5cmXjWnjtlTBxl/DTY8pQK0QHh/cTwQ849ZFsfiDoc4ISYpNQsf1c7cUz"
    "vyo2vG9QSHA8VR9AG02SmWmWZT0axmCdomKIgNubMCTCt+egJUomkKKkqtggIxEp1b8GrbqXfmyE"
    "37tJGbvKcuREwkw2giFjqDbF8kFfbGnmRXzZJcqfvV351+8Z9h9WBptlZMijgnWKqlQCoxOv/RXe"
    "c+YNXh8SKACoBw0YHxRaOJ5bMszwzWNc+tY7iDdfw1SUeJQpz5/PKLyuIby27g2rabxJJeLDaSTe"
    "8ye580u87QPctBHGrlWOHDfMxa9m9PI7GHrFbUxllzDbVcSAEa/yrgMDLXjvrY7lS4RaDM0a1BJI"
    "Yp85GskjXD9VUuE83a1ogAQTMD5o3FAXLouFaeeIlwyxbPVqRi69lCWrVpHW6h6AYAq5L+kojDWF"
    "0QgMUoTPSIKfyUNfrNRjiGMYGYK3Xq9oqvRsTHPoEoZXrGZ4xWWY+ghpNo8JA7YLI6NwyxbPZKMO"
    "tRji4BjzPCOXtPaHwTwRKjUg1wIf54VBEbbUhJ4qDWOYOXmCo7seZWrFKNMHnqDRaRNJfyNTgBRY"
    "amBjIhzJfA4RCUSqqGhwgkISeYmpwqbLYMlSxXWEwXrK5KnHORrVyHpz0HmWxiJNfCOgKVz7KmXr"
    "I4aT00oSef9ijPpDfteXd1MBwGfLBgkASAlA+HRFJIxEkCrUjWFZd47pnQ8xFcfUe12WQnHQUaU8"
    "9q9OhKQjWBUiHJEITvyBSGQI2Z8QG2HtyuCsBJYOALNPMXv0CCKOFa0erbpZ4M1EAAvDS+CSETgx"
    "LSSR+rkl951l088E5Q9hsLphv+UyHPr3lxpvu2kI6S1jaKhFexnGmLNW3U5hyHh/0hUKoCWEJBMk"
    "KHhnuGyw9NhxJIwMwbDt+nvN8xewTsEkMBw0JA+JIlKExvkbLaNAtazTedfh7/lMGgle6Oft2s6f"
    "aN7mFgMzis4G8Vmml3l/S/97Bvp/NqWVV618dsaV0u+j82DeSPh+Zc6itaJ5nJYitT09uwja50G5"
    "GZye9dd5mdy3w9y28r0t+Ix+5rMQGo9b5ZTzTvHnkbeGRZ7JfPx35AxXhxSxOrNw8Oi8DZ3POmGh"
    "6Rk4csqDkecALqTCi80XACgDpG9w+QIiCxWcVS/B3T2lxrwf2p2D+RiYdrDHn4MWVaHNGVchCyVt"
    "N/Ub3/2UMDsNEp+/dTkFqcGjTwqnzvgT5zQDa6XQLq2kQfMAKHs7eTbgAItX+05Q/fs78Kz1RdH5"
    "HFU4/L33dpRjmS+EUgWrgkV8Pa+QWSG10EsFa+Fnp+HOnQapBTU+1zoOohpMnYKtj3oeOj1fFhda"
    "UMxTqL8WABiDioTEHIOKb29mCCnQVS+5aad8Y1aZcb42qNpyddb8/SGBH3VhewcQoR3mSZEAgsE6"
    "g7VCmgndVJjr+C3evVt44FEhGvDXzvXbc+E/HJi6L4m/vM1wYgrSFNo9oZcJqRWsFVQNqAlhsPzp"
    "47yY4p2PQ7B4E+ipB2DOKZnC06nypTPKocxXh/VQRuXONQmFUwT8oA3fnFV6qrSd0lHoqYTGSABB"
    "hdT5zXZSaPdgtgNZpnztHsOd9xkywDS9SUgekYxXedOEI8eFz3/HsPeQl/ZMJ2hAKmRWsC6YAXmA"
    "h77jcZG8R13e4EQ8ABBaYr4xOohwKFO+cAaurglX1mA08kA4hRkHT2ewo6scTH3SOacwG0ypB2Tq"
    "TcyFbVinvrGR5pmbF8cAynfuF/Y8ZXjNBmXtShge8MlNpwfHTgu7DgoPPS6cnvUdojNtmOt6ILuZ"
    "jyrVZmkh6EAhDzBlNgJoaCxafOgqvIOCQ2khOKfc01Ee7ApDpvQLMw5m1Je8CrRVaTtoK3QRD4AI"
    "NpiQAKiQOkUyn7ZiKHp+A3Vl/3Nw4IhhSROWtHy/sN0Vpmah3VNElF7qNWeu60c3DeqfS1/8OoWq"
    "hk5upRzuzxgUXzDmYZDQUbEKmWjRFlOUzjyP6IBUc5X3ku9qzjzYkAeGxi0u9AnTzBdTiuKckjno"
    "pb5MriXK5AxMzuSreICzvpaYH91U6Ob2r+HEIGc+JHkmMOxTYaMzRQ0n+fShoSiQqkFxqHrJpar0"
    "gnTzpmgOnyV4drzHT/F2n+Z2L74z7ELjLvfM1vnUuBeihXOQWd/s7PR8lZibh+9Alx1hH0Gglwm9"
    "DNLM+5VC+iql8/D2jxqZKTVAo0OhGCqT0dAdVlWceAfoQdGCuVggaGxfFm3D8HmEhOvAfA6/lkt5"
    "qfiOEdanb04Va30s70a+Z2hMYaVFBlm2xX0otVbIqowHz18wL6HeUQ4VAFjcY9jMiTGRqip546SS"
    "PPlTP7/JCCFDC+bLg5Ey2bCU0cQhOMnV3ktDKnN72LWYP29iOqNkTjGZVJoaJdC5plgXcgonuMLj"
    "BwCYJ30h8j/C4pEcAHl89xuf2LBp209MZDY4awOP0ndOouKlpqI4FKN5RZdXkOXGPGAUDLuwslY3"
    "M7/wwvcSNEDoHTHFGYIRWdjtzTM8LT193vjsV/vCwJ1IJM65Q7bzs90A4cfSn3KI/oeIEe3rjkhl"
    "hARJjE+SRMjEv6aV4a8NNgwngopZhHnffzAV6eRr+fM+g1ODdUJmDakV0szQCyPN/HuZM2TOVJyd"
    "8UnPvAdcgjydMUYQvnLgwIHu2NhYXNxxxbVjI1FqHzciw/nP5cvujpaGV30tlL6f+lLOaiEu5fvS"
    "95lWpqysqZXZzvJTRtXF1yiZDzbmuwwdsbJ+z57tzxJg0vHxcbN/x90nwP2NiWOj3mf5NpKUkimM"
    "MPdGJtcMrx0apN3/eXjNDxBzRcg7IGF+qfxdvObfl7JZn5/8ltemf08VrZXAg/dlmsVJbED/fs+e"
    "7c+M+6dGXCG88fHxaGJiwm7YfPN/xUnyu2mapiIk/WLtO1vJ4V8okj4p9MmcBeqyYL7yD138g8UW"
    "W7hWaceoaprESZLZ3ra9u+67bXx8XCYmJhxVc/ff+jvZvPn/ms507zRRfFPmQeh/AvP5gDjb1uRc"
    "XD8PBXDPfy2Zj0fx0JRz9lHX671h794HJsPtrvxeSQZwGzaMDUpNvxxF8e84m1Ufm/t1eZbAqaoT"
    "MXEUxzibfT/rtN+1b99DJznLY3M5FTdsvOr1HxP4pDHRiI+dDlXNFvnOS4ZEJBIxYozBOTsF+rkf"
    "P7r9s5TNqbM+OFl9XwC3ZctrXp5R/yCifyCwzpjoF1fpC02q/qd0qk+K6Dd6aj7/+K67n6I/6+ij"
    "s3KSO0bwT2NPTuqmTHSLEVmtqkMvOAO/HM0gHBIxuzpnmrsPHPjfLvTz8IuS+TV+fP6cPuv/AYYg"
    "73G7MD/GAAAAAElFTkSuQmCC"
)

FONT_SIZES = [(4, 34), (8, 29), (12, 23), (9999, 21)]  # +30 % (v1.30)


class DisplayApp(RulesMixin):
    def __init__(self, root, config_path, port, stale, discovery_port=None):
        self.root = root
        self.port = port
        self.stale = stale
        self.config_path = config_path
        self.hints = read_config(config_path)
        self.local_nets = set(local_interfaces())
        self.self_seen = set()
        self.local = set(self.local_nets) | LOOPBACK
        self.stations = {}
        self.peers = {}
        # One lock for the station / lamp lists (UDP, discovery, window).
        self.lock = threading.RLock()
        self.node_id = base64.b16encode(os.urandom(6)).decode("ascii")
        self.used_colors = set(color for _, color in self.hints.values() if color)
        self.tracked = None
        self.settings = upd.Settings(app_dir())
        self.var_check = tk.BooleanVar(
            value=self.settings.get("check_at_startup", True)
        )
        self.var_auto = tk.BooleanVar(
            value=self.settings.get("install_automatically", False)
        )
        # Diagnostics (log) is deliberately not saved: it always starts off.
        self.var_test = tk.BooleanVar(value=False)
        self.var_keys = tk.BooleanVar(value=self.settings.get("f12_call", False))
        self.var_block = tk.BooleanVar(value=self.settings.get("block_keys", False))
        self.var_sound = tk.BooleanVar(value=self.settings.get("call_sound", True))
        self._beeped = set()  # calls already announced with a sound
        # Plain copies, read from the keyboard thread.
        self.f12_on = bool(self.var_keys.get())
        self.block_on = bool(self.var_block.get())
        self._block_active = False
        self._blocked_key = ("", 0.0)
        self._call_seen = {}  # (peer id, call time) -> local time first seen
        self._had_calls = False
        self._band_busy = None  # another PC's F12 call running on my band
        self._master_warn = ""  # "two masters" warning, empty = all fine
        self._last_ui_error = 0.0
        self._port_users = {}  # port -> other programs listening on it
        self._f12_refused = 0.0  # when my F12 was ignored (band busy)
        self.key_watcher = None
        self.alert_on = False
        self.alert_run = False  # my call was started in Run (blocks every station)
        self.urgent_on = False  # my urgent call (Shift+F2 / Shift+F4)
        self.urgent_fkey = "F4"  # which key started it
        self.urgent_time = 0.0
        self.urgent_key = ("", "")  # (band, mode) of my urgent call
        self._urgent_tx_seen = False  # my F2/F4 transmission has started
        self._shown_urgent = None
        self.alert_time = 0.0
        self.silenced = set()  # (peer id, call time) clicked away here
        self._shown_call = None
        self.hello_now = threading.Event()
        self._share = {"targets": [], "port": discovery_port or 0}
        self.var_relay = tk.BooleanVar(value=self.settings.get("share_n1mm", True))
        self.relay_on = bool(self.var_relay.get())
        self.test_on = False  # plain copy, safe to read from the UDP thread
        self.test_log_path = os.path.join(app_dir(), TEST_LOG)
        self.my_alias = self.settings.get_text("station", "alias", "")[:MAX_ALIAS]
        self.started = time.time()
        self.n1mm_ini = self.settings.get_text("n1mm", "ini", "") or n1mm_ini_path()
        self.n1mm_master = None
        self._ini_mtime = None
        self._ini_checked = 0.0
        self.color_map = {}  # automatic colors decided by the master
        # F12 block time: set on the master, used by every lamp.
        self.block_limit = clamp_block(_float(self.settings.get_text("network", "block_time", "")))
        self.block_limit_run = clamp_block(
            _float(self.settings.get_text("network", "block_time_run", "")), BLOCK_LIMIT_RUN
        )
        self._i_am_master = False
        self._master_peer = None
        # The ping-pong role always follows N1MM (Run = RUN, S&P = INB);
        # an old fixed "role" in pingpong.ini is ignored.
        self._last_role = ""
        self._go_time = 0.0  # when RUN's last transmission on my band ended
        self._go_band = ""
        self._go_beeped = 0.0
        self.var_go_sound = tk.BooleanVar(value=self.settings.get("go_sound", False))
        # Ping-pong lock on/off: set on the master, used by every lamp.
        self.pp_enabled = self.settings.get_text("network", "pingpong", "yes") != "no"
        self._slots = {}  # band -> [holder station ip, time of its last TX start]
        self._pp_locked = False
        self._pp_holder = None
        self._pp_left = 0
        self.my_color = self.settings.get_text("station", "color", "")
        self.my_color_time = _float(
            self.settings.get_text("station", "color_time", "0")
        )
        self._color_dialog = None
        self._ui_calls = []
        self._busy = False
        self._status = ""
        self._save_job = None
        self._build()
        self._restore_window()
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=listener_loop,
            args=("0.0.0.0", port, self.on_packet, self.stop),
            daemon=True,
        )
        self.thread.start()
        if discovery_port:
            self.discovery = threading.Thread(
                target=discovery_loop,
                args=(
                    discovery_port,
                    self.node_id,
                    self._hello_state,
                    self.on_networks,
                    self.on_hello,
                    self.stop,
                    self.hello_now,
                    self._share,
                    self.on_relay,
                    self.on_command,
                ),
                daemon=True,
            )
            self.discovery.start()
        threading.Thread(
            target=port_watch_loop,
            args=(self.port, self._port_users, self.stop),
            daemon=True,
        ).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._update)
        if keys.supported():
            # Always: the ping-pong lock may need it whenever N1MM is in S&P.
            self.root.after(500, self._keys_toggled)
        if upd.is_frozen():
            upd.cleanup_leftovers(upd.exe_path())
            if self.var_check.get():
                self.root.after(3000, lambda: self._check_updates(manual=False))

    def _remote_update(self):
        """'Update all lamps' from the Overview: install the newest release
        without asking (the master's operator asked for it)."""
        if not upd.is_frozen():
            self._show_status("update request ignored - running from source")
            return
        if self._busy:
            return
        self._busy = True
        self._status = "update requested by the master – checking…"

        def done(releases, error):
            self._busy = False
            self._status = ""
            if error is not None:
                self._show_status("update check failed: {}".format(error))
                return
            new = upd.newer_release(releases, __version__)
            if new is None:
                self._show_status("already the newest version (v{})".format(__version__))
                return
            self._install(new)

        self._run_bg(upd.fetch_releases, done)

    def _show_status(self, text, seconds=10):
        self._status = text
        self.root.after(int(seconds * 1000), lambda: (
            setattr(self, "_status", "") if self._status == text else None))

    def _run_bg(self, work, done):
        """Run work() in a thread, then done(result, error) in the UI."""

        def runner():
            try:
                result, error = work(), None
            except Exception as exc:  # reported to the user in done()
                result, error = None, exc
            self._ui_calls.append(lambda: done(result, error))

        threading.Thread(target=runner, daemon=True).start()

    def _show_options(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_checkbutton(
            label="Compact mode",
            accelerator="Shift+F",
            variable=self.var_compact,
            command=lambda: self.root.after(
                50, lambda: self._set_compact(bool(self.var_compact.get()))
            ),
        )
        menu.add_separator()
        menu.add_command(
            label="My name…  ({})".format(self.my_alias or "PC name"),
            command=self._choose_alias,
        )
        menu.add_command(
            label="My color…  ({})".format(self.my_color or "automatic"),
            command=self._choose_color,
        )
        if self._i_am_master:
            menu.add_command(
                label="F12 block time, S&P call…  ({:g} s, blocks S&P)".format(self.block_limit),
                command=lambda: self._choose_block_time(run=False),
            )
            menu.add_command(
                label="F12 block time, Run call…  ({:g} s, blocks all)".format(
                    self.block_limit_run),
                command=lambda: self._choose_block_time(run=True),
            )
        else:
            master = self.peer_name(self._master_peer) if self._master_peer else "?"
            menu.add_command(
                label="F12 block time: S&P call {:g} s, Run call {:g} s  (set on master {})".format(
                    self.block_limit, self.block_limit_run, master,
                ),
                state=tk.DISABLED,
            )
        menu.add_checkbutton(
            label="F12 call – blink stations on my band (Shift+F2/F4 = urgent)",
            variable=self.var_keys,
            command=self._keys_toggled,
        )
        menu.add_checkbutton(
            label="Block my F-keys while another station calls (F12)",
            variable=self.var_block,
            command=self._keys_toggled,
        )
        menu.add_checkbutton(
            label="Sound on F12 call",
            variable=self.var_sound,
            command=lambda: self.settings.set("call_sound", self.var_sound.get()),
        )
        menu.add_checkbutton(
            label="Sound on GO (ping-pong: RUN has finished)",
            variable=self.var_go_sound,
            command=lambda: self.settings.set("go_sound", self.var_go_sound.get()),
        )
        menu.add_checkbutton(
            label="Share my N1MM with other lamps (all networks)",
            variable=self.var_relay,
            command=self._relay_toggled,
        )
        menu.add_separator()
        if self._i_am_master:
            var = tk.BooleanVar(value=self.pp_enabled)
            menu.add_checkbutton(
                label="Ping-pong lock (RUN first, inband stations take turns) – for all PCs",
                variable=var, command=lambda: self._set_pingpong(bool(var.get())),
            )
        else:
            master = self.peer_name(self._master_peer) if self._master_peer else "?"
            menu.add_command(
                label="Ping-pong lock: {}  (set on master {})".format(
                    "ON" if self.pp_enabled else "OFF – everybody may transmit", master),
                state=tk.DISABLED,
            )
        now_role = self._my_role_now()
        menu.add_command(
            label="Ping-pong role: {}  (from N1MM: Run = RUN, S&P = INB)".format(
                now_role if now_role != "off" else "unknown"),
            state=tk.DISABLED,
        )
        menu.add_separator()
        frozen = upd.is_frozen()
        state = tk.NORMAL if frozen and not self._busy else tk.DISABLED
        menu.add_command(
            label="Check for updates",
            state=state,
            command=lambda: self._check_updates(manual=True),
        )
        menu.add_command(
            label="Switch version…", state=state, command=self._switch_version
        )
        backup = upd.find_backup(upd.exe_path()) if frozen else None
        menu.add_command(
            label="Revert to previous version"
            + (" (v{})".format(backup[0]) if backup else ""),
            state=state if backup else tk.DISABLED,
            command=self._revert,
        )
        menu.add_separator()
        menu.add_checkbutton(
            label="Check for updates at startup",
            variable=self.var_check,
            command=lambda: self.settings.set(
                "check_at_startup", self.var_check.get()
            ),
        )
        menu.add_checkbutton(
            label="Install updates automatically",
            variable=self.var_auto,
            command=lambda: self.settings.set(
                "install_automatically", self.var_auto.get()
            ),
        )
        menu.add_separator()
        menu.add_command(
            label="Allow PingPong in Windows Firewall…",
            state=tk.NORMAL if sys.platform == "win32" else tk.DISABLED,
            command=self._allow_firewall,
        )
        menu.add_checkbutton(
            label="Diagnostics (log to file)",
            variable=self.var_test,
            command=self._test_toggled,
        )
        menu.add_separator()
        menu.add_command(
            label="PingPong v{}{}".format(
                __version__, "" if frozen else "  (source - updates only in exe)"
            ),
            state=tk.DISABLED,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _check_updates(self, manual):
        if self._busy:
            return
        self._busy = True
        if manual:
            self._status = "checking for updates…"
        self._run_bg(
            upd.fetch_releases,
            lambda rels, err: self._on_releases(rels, err, manual),
        )

    def _on_releases(self, releases, error, manual):
        self._busy = False
        self._status = ""
        if error is not None:
            if manual:
                messagebox.showerror(
                    "PingPong",
                    "Could not check for updates:\n{}".format(error),
                    parent=self.root,
                )
            return
        new = upd.newer_release(releases, __version__)
        if new is None:
            if manual:
                messagebox.showinfo(
                    "PingPong",
                    "You have the latest version (v{}).".format(__version__),
                    parent=self.root,
                )
            return
        if not manual and self.var_auto.get():
            self._install(new)
            return
        if messagebox.askyesno(
            "PingPong update",
            "PingPong v{} is available (you have v{}).\n\n"
            "Update now? PingPong will restart.\n"
            "You can switch back any time under Options.".format(
                new["version"], __version__
            ),
            parent=self.root,
        ):
            self._install(new)

    def _old_version_note(self, version):
        """Warning when going to a version without this Options menu."""
        if upd.parse_version(version) >= (1, 6):
            return ""
        return (
            "\n\nNote: v{} has no Options menu. To come back, double-click\n"
            "{} in the PingPong folder\n"
            "or download the newest release from GitHub.".format(
                version,
                os.path.basename(upd.backup_path(upd.exe_path(), __version__)),
            )
        )

    def _install(self, release):
        if self._busy:
            return
        self._busy = True
        target = upd.exe_path()
        self._status = "downloading v{}…".format(release["version"])

        def progress(done, total):
            if total:
                self._status = "downloading v{}… {}%".format(
                    release["version"], int(done * 100 / total)
                )

        def work():
            new_file = upd.download(release, target, progress)
            upd.swap_in(target, __version__, new_file)

        def done(_, error):
            self._busy = False
            self._status = ""
            if error is not None:
                messagebox.showerror(
                    "PingPong",
                    "Update to v{} failed:\n{}\n\n"
                    "Your current version v{} is unchanged.".format(
                        release["version"], error, __version__
                    ),
                    parent=self.root,
                )
                return
            self._restart(target)

        self._run_bg(work, done)

    def _switch_version(self):
        if self._busy:
            return
        self._busy = True
        self._status = "getting versions…"

        def done(releases, error):
            self._busy = False
            self._status = ""
            if error is not None:
                messagebox.showerror(
                    "PingPong",
                    "Could not get the list of versions:\n{}".format(error),
                    parent=self.root,
                )
                return
            self._version_dialog(releases)

        self._run_bg(upd.fetch_releases, done)

    def _version_dialog(self, releases):
        dlg = tk.Toplevel(self.root)
        dlg.title("Switch version")
        dlg.attributes("-topmost", True)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        tk.Label(
            dlg, text="Installed: v{}".format(__version__), font=("Segoe UI", 9)
        ).pack(padx=10, pady=(8, 4), anchor=tk.W)
        box = tk.Listbox(dlg, height=min(10, max(3, len(releases))), width=34)
        for rel in releases:
            mark = "   (installed)" if rel["version"] == __version__ else ""
            box.insert(tk.END, "v{}   {}{}".format(rel["version"], rel["date"], mark))
        box.pack(padx=10)
        if releases:
            box.selection_set(0)

        def install():
            sel = box.curselection()
            if not sel:
                return
            rel = releases[sel[0]]
            if rel["version"] == __version__:
                messagebox.showinfo(
                    "PingPong", "v{} is already installed.".format(__version__),
                    parent=dlg,
                )
                return
            if not messagebox.askyesno(
                "PingPong",
                "Install v{} now? PingPong will restart.{}".format(
                    rel["version"], self._old_version_note(rel["version"])
                ),
                parent=dlg,
            ):
                return
            dlg.destroy()
            self._install(rel)

        buttons = tk.Frame(dlg)
        buttons.pack(pady=8)
        tk.Button(buttons, text="Install", width=10, command=install).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="Cancel", width=10, command=dlg.destroy).pack(
            side=tk.LEFT, padx=4
        )
        box.bind("<Double-Button-1>", lambda e: install())

    def _revert(self):
        target = upd.exe_path()
        backup = upd.find_backup(target)
        if not backup or self._busy:
            return
        if not messagebox.askyesno(
            "PingPong",
            "Switch back to v{}? PingPong will restart.\n"
            "(v{} is kept, so you can switch forward again.){}".format(
                backup[0], __version__, self._old_version_note(backup[0])
            ),
            parent=self.root,
        ):
            return
        try:
            upd.revert(target, __version__)
        except OSError as exc:
            messagebox.showerror(
                "PingPong", "Could not switch back:\n{}".format(exc), parent=self.root
            )
            return
        self._restart(target)

    def _restart(self, target):
        try:
            upd.relaunch(target, sys.argv[1:])
        except OSError as exc:
            messagebox.showerror(
                "PingPong",
                "Installed, but could not restart automatically:\n{}\n\n"
                "Please start {} again.".format(exc, os.path.basename(target)),
                parent=self.root,
            )
        self.on_close()

    def _station(self, src, station_name):
        ip = src
        if ip in self.stations:
            return self.stations[ip]
        hint_label, hint_color = self.hints.get(ip, ("", ""))
        color = hint_color
        if not color:
            free = [c for c in COLOR_PALETTE if c not in self.used_colors]
            color = free[0] if free else COLOR_PALETTE[-1]
            self.used_colors.add(color)
        label = hint_label or station_name or ip
        station = Station(ip, label, color)
        self.stations[ip] = station
        return station

    @locked
    def on_packet(self, src, data, relayed=False):
        is_radio, caption, station_name, is_tx, freq, mode = packet_info(data)
        if self.test_on:
            if src in self.local:
                self._test_log(src, data)
            elif is_radio:
                self._test_log(src, data, "relayed" if relayed else "direct")
        if not relayed:
            if src in self.local:
                if is_radio and self.relay_on:
                    send_relay(self._share, make_relay(self.node_id, data))
            else:
                src = self._canon_ip(src)
        station = self._station(src, station_name)
        if not relayed:
            station.direct_seen = time.time()
        station.count += 1
        if not station.label or station.label == station.ip:
            station.label = station_name or station.label
        if is_radio:
            running = radio_running(data)
            if running is not None:
                station.running = running
            started = is_tx and not station.tx
            ended = station.tx and not is_tx
            station.tx = is_tx
            station.last_seen = time.time()
            if caption:
                if caption != station.msg:
                    station.msg_time = time.time()
                station.msg = caption
            if freq:
                station.freq = freq
            if mode:
                station.mode = mode
            station.key = group_of(station.freq, station.mode)
            if station.ip in self.local:
                self.tracked = station.key
            if started:
                self._pingpong_tx(station)
            if ended:
                self._pingpong_end(station)

    def _set_title(self, master):
        """Title bar: 'MASTER' on the master PC only."""
        title = (
            "PingPong  -  MASTER  v{}" if master else "PingPong  v{}"
        ).format(__version__)
        if getattr(self, "_title", None) != title:
            self._title = title
            self.root.title(title)

    def _update_master(self, owners, now):
        self._check_n1mm_master(now)
        master = self._pick_master(now)
        was = self._i_am_master
        self._i_am_master = master == "me"
        self._master_peer = None if self._i_am_master else master
        self._set_title(self._i_am_master)
        if self._i_am_master:
            new = self._assign_colors(owners, now)
            if new != self.color_map:
                self.color_map = new
                self.hello_now.set()
        else:
            if master.colors:
                self.color_map = dict(master.colors)
            if master.block_time and clamp_block(master.block_time) != self.block_limit:
                self.block_limit = clamp_block(master.block_time)
                # Remember it, so this PC keeps it if it becomes master.
                self.settings.set_text("network", "block_time", "{:g}".format(self.block_limit))
            if master.pingpong in ("1", "0") and (master.pingpong == "1") != self.pp_enabled:
                self.pp_enabled = master.pingpong == "1"
                self.settings.set_text("network", "pingpong", "yes" if self.pp_enabled else "no")
                self._key_event("ping-pong lock {} (set on the master)".format(
                    "ON" if self.pp_enabled else "OFF"))
            run = master.block_time_run
            if run and clamp_block(run, BLOCK_LIMIT_RUN) != self.block_limit_run:
                self.block_limit_run = clamp_block(run, BLOCK_LIMIT_RUN)
                self.settings.set_text(
                    "network", "block_time_run", "{:g}".format(self.block_limit_run)
                )
        if was != self._i_am_master:
            self._key_event("PingPong master: {}".format(
                "this PC" if self._i_am_master else self.peer_name(master)
            ))

    def _color_for(self, station, owners, now):
        if station.ip in self.local:
            if self.my_color and owners.get(self.my_color.lower()) == "me":
                return self.my_color
        else:
            for peer in self._fresh_peers(now):
                if station.ip in peer.ips and peer.color:
                    if owners.get(peer.color.lower()) is peer:
                        return peer.color
                    break
        auto = self.color_map.get(self.color_key(station, now))
        if auto and auto.lower() not in owners:
            return auto
        # Fallback (no master list yet) - never a color another PC has chosen.
        if (station.color or "").lower() in owners:
            taken = set(owners) | set(
                (st.color or "").lower()
                for st in list(self.stations.values())
                if st is not station
            )
            free = [c for c in COLOR_PALETTE if c.lower() not in taken]
            if free:
                station.color = free[0]
        return station.color

    def _my_color_note(self, owners):
        if not self.my_color:
            return ""
        owner = owners.get(self.my_color.lower())
        if owner == "me" or owner is None:
            return ""
        return "color {} is taken ({})".format(self.my_color, self.peer_name(owner))

    def _choose_color(self):
        if self._color_dialog is not None:
            try:
                self._color_dialog.lift()
                return
            except tk.TclError:
                self._color_dialog = None
        now = time.time()
        owners = self._color_owners(now)
        dlg = tk.Toplevel(self.root)
        self._color_dialog = dlg
        dlg.title("My color")
        dlg.attributes("-topmost", True)
        dlg.transient(self.root)
        dlg.resizable(False, False)

        def closed():
            self._color_dialog = None
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", closed)
        tk.Label(
            dlg,
            text="Color of this PC's station on every PingPong lamp.\n"
            "Colors chosen by other PCs are not available.",
            font=("Segoe UI", 9),
            justify=tk.LEFT,
        ).pack(padx=10, pady=(8, 6), anchor=tk.W)
        grid = tk.Frame(dlg)
        grid.pack(padx=10)

        def pick(color):
            if color != self.my_color:
                self.my_color = color
                self.my_color_time = time.time() if color else 0.0
                self.settings.set_text("station", "color", color)
                self.settings.set_text(
                    "station", "color_time", "{:.3f}".format(self.my_color_time)
                )
            closed()

        for i, color in enumerate(COLOR_PALETTE):
            owner = owners.get(color.lower())
            taken = owner is not None and owner != "me"
            mine = color == self.my_color and not taken
            text = self.peer_name(owner) if taken else ("✓ " + color if mine else color)
            tk.Button(
                grid,
                text=text,
                width=12,
                bg=color,
                activebackground=color,
                fg=self.text_on(color),
                activeforeground=self.text_on(color),
                disabledforeground=self.text_on(color),
                relief=tk.SUNKEN if mine else tk.RAISED,
                state=tk.DISABLED if taken else tk.NORMAL,
                command=lambda c=color: pick(c),
            ).grid(row=i // 5, column=i % 5, padx=2, pady=2)
        if self.my_color and self.my_color not in COLOR_PALETTE:
            tk.Label(
                dlg,
                text="✓ my color  {}".format(self.my_color),
                bg=self.my_color,
                fg=self.text_on(self.my_color),
                font=("Segoe UI", 9, "bold"),
                padx=8,
                pady=3,
            ).pack(pady=(6, 0))

        def more():
            try:
                _, chosen = colorchooser.askcolor(
                    color=self.my_color or "#ff0000",
                    title="PingPong - my color",
                    parent=dlg,
                )
            except tk.TclError:
                chosen = None
            if not chosen:
                return
            chosen = chosen.lower()
            clash = self._too_similar(chosen, self._color_owners(time.time()))
            if clash:
                messagebox.showwarning(
                    "PingPong",
                    "This color is too similar to the color of {}.\n"
                    "Please choose a different one.".format(clash),
                    parent=dlg,
                )
                return
            pick(chosen)

        buttons = tk.Frame(dlg)
        buttons.pack(pady=8)
        tk.Button(
            buttons, text="More colors…", width=12, command=more
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            buttons, text="Automatic", width=10, command=lambda: pick("")
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="Close", width=10, command=closed).pack(
            side=tk.LEFT, padx=4
        )

    def _peer_rows(self, now):
        """Footer notes for PingPong PCs whose N1MM traffic we do not get."""
        station_ips = set(self.stations)
        rows = []
        for peer in list(self.peers.values()):
            if (now - peer.last_seen) > PEER_TIMEOUT:
                continue
            if peer.ips & station_ips:
                continue
            if peer.local_n1mm:
                # Its N1MM runs, but the broadcast does not reach this PC:
                # usually a wrong Broadcast Address in that PC's N1MM.
                rows.append("{}: N1MM not reaching here".format(self.peer_name(peer)))
            else:
                rows.append("{}: no N1MM".format(self.peer_name(peer)))
        rows.extend(self._version_rows(now))
        rows.extend(self._no_lamp_rows(now))
        return rows

    def _allow_firewall(self):
        """Add an inbound firewall rule for the lamp's UDP ports (asks UAC)."""
        ports = ",".join(
            str(p) for p in (self.port, self._share["port"]) if p
        )
        if not messagebox.askyesno(
            "PingPong - Windows Firewall",
            "Allow incoming UDP {} (N1MM data and the other lamps) on private "
            "and domain networks?\n\nWindows asks for administrator rights. "
            "The rule is called \"{}\"; an older rule with that name is "
            "replaced.".format(ports, FIREWALL_RULE),
            parent=self.root,
        ):
            return
        rule = (
            'netsh advfirewall firewall delete rule name="{0}" >nul 2>&1 & '
            'netsh advfirewall firewall add rule name="{0}" dir=in action=allow '
            "protocol=UDP localport={1} profile=private,domain"
        ).format(FIREWALL_RULE, ports)
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", '/c ' + rule, None, 0
            )
        except Exception as exc:
            result, rule = 0, repr(exc)
        if result > 32:
            messagebox.showinfo(
                "PingPong - Windows Firewall",
                "Firewall rule \"{}\" added (UDP {}).".format(FIREWALL_RULE, ports),
                parent=self.root,
            )
        else:
            messagebox.showwarning(
                "PingPong - Windows Firewall",
                "The rule was not added (no administrator rights given).",
                parent=self.root,
            )

    def _choose_block_time(self, run=False):
        """Master only: how long an F12 call blocks the other PCs.

        run=False: call from an S&P station (blocks S&P stations);
        run=True: call from a Run station (blocks every station).
        """
        if not self._i_am_master:
            return
        current = self.block_limit_run if run else self.block_limit
        value = simpledialog.askinteger(
            "PingPong - F12 block time ({})".format("Run call" if run else "S&P call"),
            "How many seconds an F12 call from a station in {} lasts\n"
            "(blinking and F-key block on {}), {}-{} s.\n"
            "Used by all lamps.".format(
                "Run" if run else "S&P",
                "ALL other stations on the band" if run else "the S&P stations on the band",
                *BLOCK_RANGE
            ),
            initialvalue=int(current),
            minvalue=BLOCK_RANGE[0],
            maxvalue=BLOCK_RANGE[1],
            parent=self.root,
        )
        if value is None:
            return
        if run:
            self.block_limit_run = float(value)
            self.settings.set_text("network", "block_time_run", "{:g}".format(value))
        else:
            self.block_limit = float(value)
            self.settings.set_text("network", "block_time", "{:g}".format(value))
        self._key_event("F12 block time ({}) set to {:g} s".format(
            "Run" if run else "S&P", value))
        self.hello_now.set()  # tell all lamps right away

    def _show_urgent(self, peer, owners, now):
        """Intense flash: lamp and border swap caller color / white fast."""
        self._shown_urgent = peer
        station = self._peer_station(peer)
        color = self._call_color(peer, owners, now)
        name = self.peer_name(peer) if peer.alias or station is None else self.station_name(station)
        lit = int(now / URGENT_HALF) % 2 == 0
        self._set_bg(color if lit else "white")
        want = "white" if lit else color
        if self.canvas.cget("highlightbackground") != want:
            self.canvas.configure(highlightbackground=want, highlightcolor=want)
        # Caller's alias + the F2/F4 message caption it is sending right now
        # (N1MM's FunctionKeyCaption); "MSG" until the caption arrives.
        msg = ""
        seen = self._call_seen.get(("urgent", peer.id, peer.urgent_time), now)
        if station is not None and station.msg and (
            station.tx or station.msg_time >= seen - 1.0
        ):
            msg = station.msg
        shown = "{} · {}".format(self.peer_name(peer), msg or "MSG")
        if self.canvas.itemcget(self.text_id, "text") != shown:
            self.canvas.itemconfigure(self.text_id, text=shown)
            self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
        self._set_who("URGENT: " + self.peer_name(peer))
        self._freq_text = freq_khz(station.freq) if station is not None else ""
        self.info.configure(
            text="URGENT call from {}  ·  {} {}  ·  while it transmits  ·  click = silence".format(
                self.peer_name(peer), peer.urgent_band, peer.urgent_mode,
            )
        )

    def _choose_alias(self):
        current = self.my_alias
        text = simpledialog.askstring(
            "PingPong - my name",
            "Short name for this PC on every lamp (e.g. RUN1).\n"
            "Max {} characters; empty = use the N1MM computer name.".format(MAX_ALIAS),
            initialvalue=current,
            parent=self.root,
        )
        if text is None:
            return
        text = " ".join(text.split())[:MAX_ALIAS]
        if text:
            for peer in self._fresh_peers(time.time()):
                if peer.alias.lower() == text.lower() and not getattr(peer, "same_pc", False):
                    messagebox.showwarning(
                        "PingPong",
                        "The name {} is already used by {}.\n"
                        "Please choose a different one.".format(text, peer.host),
                        parent=self.root,
                    )
                    return
        self.my_alias = text
        self.settings.set_text("station", "alias", text)
        self.hello_now.set()  # tell the other lamps right away

    def _band_mode(self, st):
        return group_label(st.key) or (
            "{} {}".format(band_of(st.freq) or "?", st.mode).strip() if st.mode else "?"
        )

    def role_label(self, st):
        """RUN or INB: the ping-pong role if set, else from N1MM's mode
        (Run = RUN, S&P = INB); "" if unknown."""
        return self.role_of(st) or {True: "RUN", False: "INB"}.get(st.running, "")

    def station_host(self, st):
        """Windows computer name of the PC behind a station, if known."""
        if st.ip in self.local:
            return socket.gethostname()
        for peer in self._fresh_peers(time.time()):
            if st.ip in peer.ips and not getattr(peer, "same_pc", False):
                return peer.host
        return st.label

    def station_title(self, st):
        """'RUN1 (OO840G4)' - name plus hostname when they differ."""
        name, host = self.station_name(st), self.station_host(st)
        if host and host.lower() != name.lower():
            return "{} ({})".format(name, host)
        return name

    def _station_state(self, st):
        """'14 CW RUN' - band, mode and RUN/INB of a station."""
        return "{} {}".format(self._band_mode(st), self.role_label(st)).strip()

    def _station_rows(self, now):
        """One entry per station (newest per name), this PC's first."""
        newest = {}
        for st in list(self.stations.values()):
            if now - st.last_seen > STATION_FORGET:
                continue
            name = (st.label or st.ip).lower()
            if name not in newest or st.last_seen > newest[name].last_seen:
                newest[name] = st
        mine = [st for st in newest.values() if st.ip in self.local]
        others = sorted(
            (st for st in newest.values() if st.ip not in self.local),
            key=lambda st: self.station_name(st).lower(),
        )
        rows = [
            "{}{} (me{})".format(
                (self.my_alias + " ") if self.my_alias else "", self._station_state(st),
                ", master" if self._i_am_master else "",
            )
            for st in mine[:1]
        ]
        mp = self._master_peer
        rows += [
            "{} {}{}".format(
                self.station_name(st), self._station_state(st),
                " (master)" if mp is not None and st.ip in mp.ips else "",
            )
            for st in others
        ]
        return rows

    def _is_tracked(self, station):
        return bool(self.tracked) and station.key == self.tracked

    def _build(self):
        self._set_title(False)
        self.root.attributes("-topmost", True)
        try:
            self.app_icon = tk.PhotoImage(
                data=base64.b64decode(APP_ICON_B64.encode("ascii"))
            )
            self.root.iconphoto(True, self.app_icon)  # title bar + taskbar
        except tk.TclError:
            pass
        frame = tk.Frame(self.root, padx=6, pady=4)
        frame.pack()
        top = tk.Frame(frame)
        top.pack(fill=tk.X)
        self.frame, self.top = frame, top
        self._compact = False
        self.var_compact = tk.BooleanVar(value=False)
        self._drag = None
        # Shift+F: compact mode (no title bar / margins, info on the lamp).
        self.root.bind("<Key-F>", self._toggle_compact)
        # Who is sending on this band right now - drawn on the lamp's
        # top-left corner (see c_who), opposite the frequency.
        self._who_text = ""
        self.help_icon = tk.PhotoImage(
            data=base64.b64decode(HELP_ICON_B64.encode("ascii"))
        )
        self.help_link = tk.Label(top, image=self.help_icon, cursor="hand2")
        self.help_link.pack(side=tk.RIGHT)
        self.help_link.bind("<Button-1>", lambda e: self._open_help())
        self.options_link = tk.Label(
            top, text="Options ▾", font=("Segoe UI", 8), cursor="hand2"
        )
        self.options_link.pack(side=tk.RIGHT, padx=(0, 6))
        self.options_link.bind("<Button-1>", self._show_options)
        self.canvas = tk.Canvas(
            frame,
            width=330,
            height=88,
            bg=COLOR_IDLE,
            highlightthickness=CALL_BORDER,
            highlightbackground=self.root.cget("bg"),
            highlightcolor=self.root.cget("bg"),
        )
        self._window_bg = self.root.cget("bg") or "SystemButtonFace"
        self.canvas.pack(fill=tk.X)
        self.text_id = self.canvas.create_text(
            165, 44, text="—", font=("Segoe UI", 26, "bold"), fill="white"
        )
        self.canvas.bind("<Configure>", self._recenter_text)
        # Left click = silence a call; in compact mode dragging moves the
        # window. Right click = Options menu.
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._show_options)
        # Small texts drawn on the lamp itself, used in compact mode.
        # (Canvas coordinates include the call border, so stay inside it.)
        self.c_who = self.canvas.create_text(
            CALL_BORDER + 6, CALL_BORDER + 3, text="", anchor="nw",
            font=("Segoe UI", 8, "bold"), fill="white",
        )
        self.c_freq = self.canvas.create_text(
            330 - 6, CALL_BORDER + 3, text="", anchor="ne",
            font=("Segoe UI", 8, "bold"), fill="white",
        )
        self._freq_text = ""
        self.c_info = self.canvas.create_text(
            165, 84, text="", anchor="s", font=("Segoe UI", 7),
            fill="white", width=320, justify="center",
        )
        self.info = tk.Label(
            frame, text="", font=("Segoe UI", 8), justify=tk.CENTER, wraplength=340
        )
        self.info.pack(fill=tk.X)
        self._info_fg = self.info.cget("fg")

    def _rgb(self, color):
        """(r, g, b) 0-255 of any Tk color name or #rrggbb, or None."""
        try:
            return tuple(v // 256 for v in self.root.winfo_rgb(color))
        except (tk.TclError, TypeError, ValueError):
            return None

    def text_on(self, color):
        """Readable text color on this background: black on light colors,
        white on dark ones."""
        rgb = self._rgb(color)
        if rgb is None:
            return "white"
        r, g, b = rgb
        return "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "white"

    def _set_who(self, text):
        self._who_text = text

    def _set_bg(self, bg):
        """Lamp background + matching text color."""
        if self.canvas.cget("bg") != bg:
            self.canvas.configure(bg=bg)
            self.canvas.itemconfigure(self.text_id, fill=self.text_on(bg))

    def _too_similar(self, color, owners):
        """Host of another PC whose chosen color looks like `color`."""
        rgb = self._rgb(color)
        if rgb is None:
            return None
        for other, owner in owners.items():
            if owner == "me":
                continue
            orgb = self._rgb(other)
            if orgb and sum((a - b) ** 2 for a, b in zip(rgb, orgb)) ** 0.5 < SIMILAR_COLOR:
                return self.peer_name(owner)
        return None

    def _recenter_text(self, event=None):
        w = event.width if event else self.canvas.winfo_width()
        h = event.height if event else self.canvas.winfo_height()
        # In compact mode the status line sits on the lamp: lift the text.
        lift = 9 if self._compact else 0
        self.canvas.coords(self.text_id, w / 2.0, h / 2.0 - lift)
        inset = CALL_BORDER
        self.canvas.coords(self.c_who, inset + 6, inset + 3)
        self.canvas.coords(self.c_freq, w - inset - 6, inset + 3)
        self.canvas.coords(self.c_info, w / 2.0, h - inset - 2)
        self.canvas.itemconfigure(self.c_info, width=max(50, w - 2 * inset - 12))

    def _toggle_compact(self, event=None):
        """Shift+F: lamp only, no title bar or margins, info drawn on the
        lamp; Shift+F again: normal window. Same size and place."""
        self._set_compact(not self._compact)
        return "break"

    def _set_compact(self, on, pos=None):
        if pos is None:
            pos = (self.root.winfo_rootx(), self.root.winfo_rooty())
        x, y = pos
        self._compact = on
        self.var_compact.set(on)
        if self._compact:
            self.top.pack_forget()
            self.info.pack_forget()
            self.frame.configure(padx=0, pady=0)
            self.root.overrideredirect(True)
            self.root.geometry("+{}+{}".format(x, y))
        else:
            self.root.overrideredirect(False)
            self.canvas.pack_forget()
            self.frame.configure(padx=6, pady=4)
            self.top.pack(fill=tk.X)
            self.canvas.pack(fill=tk.X)
            self.info.pack(fill=tk.X)
            self.root.geometry("+{}+{}".format(x, y))
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self._recenter_text()
        self._sync_compact()
        self.root.after(300, self._save_window)

    def _window_pos(self):
        """(x, y) of the window as used by geometry('+x+y')."""
        m = re.match(r"\d+x\d+([+-]-?\d+)([+-]-?\d+)", self.root.geometry())
        if not m:
            return None
        return int(m.group(1).replace("+-", "-")), int(m.group(2).replace("+-", "-"))

    def _save_window(self):
        """Remember place and compact mode in pingpong.ini."""
        pos = self._window_pos()
        if pos is None:
            return
        want = ("{}".format(pos[0]), "{}".format(pos[1]), "yes" if self._compact else "no")
        have = tuple(
            self.settings.get_text("window", k, "") for k in ("x", "y", "compact")
        )
        if want != have:
            for key, value in zip(("x", "y", "compact"), want):
                self.settings.set_text("window", key, value)

    def _on_root_configure(self, event):
        """Window moved: save the new place a moment later (debounced)."""
        if event.widget is not self.root:
            return
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(1500, self._save_window_job)

    def _save_window_job(self):
        self._save_job = None
        self._save_window()

    def _restore_window(self):
        """Put the lamp back where it was, in the mode it was in."""
        try:
            x = int(self.settings.get_text("window", "x", ""))
            y = int(self.settings.get_text("window", "y", ""))
        except ValueError:
            return
        if not on_screen(x + 40, y + 20):
            return  # e.g. that monitor is gone: keep the default place
        if self.settings.get_text("window", "compact", "no") == "yes":
            self._set_compact(True, pos=(x, y))
        else:
            self.root.geometry("+{}+{}".format(x, y))

    def _sync_compact(self):
        """Texts drawn on the lamp: sender name (top-left) and frequency
        (top-right) always; the status line in compact mode."""
        who = self._who_text
        info = self.info.cget("text") if self._compact else ""
        fg = self.text_on(self.canvas.cget("bg"))
        for item, text in (
            (self.c_who, who), (self.c_info, info), (self.c_freq, self._freq_text)
        ):
            if self.canvas.itemcget(item, "text") != text:
                self.canvas.itemconfigure(item, text=text)
            if self.canvas.itemcget(item, "fill") != fg:
                self.canvas.itemconfigure(item, fill=fg)

    def _press(self, event):
        self.root.focus_force()  # so Shift+F works after a click
        self._drag = (event.x_root, event.y_root,
                      self.root.winfo_x(), self.root.winfo_y(), False)

    def _motion(self, event):
        if not self._compact or self._drag is None:
            return
        x0, y0, wx, wy, _ = self._drag
        dx, dy = event.x_root - x0, event.y_root - y0
        if abs(dx) + abs(dy) > 3:
            self._drag = (x0, y0, wx, wy, True)
            self.root.geometry("+{}+{}".format(wx + dx, wy + dy))

    def _release(self, event):
        moved = self._drag is not None and self._drag[4]
        self._drag = None
        if not moved:
            self._silence_call()
        else:
            self._save_window()

    def _open_help(self):
        try:
            webbrowser.open(HELP_URL)
        except Exception:
            pass

    def _font_for(self, text):
        length = len(text)
        for limit, size in FONT_SIZES:
            if length <= limit:
                return ("Segoe UI", size, "bold")
        return ("Segoe UI", 16, "bold")

    def _update(self):
        """Display loop: one error is logged, the loop always goes on."""
        try:
            with self.lock:
                self._update_body()
        except Exception:
            now = time.time()
            if now - self._last_ui_error > 60:  # don't flood the log
                self._last_ui_error = now
                log_error("display")
        finally:
            self.root.after(120, self._update)

    def _update_body(self):
        # Run finished background work as separate events, so a dialog it
        # opens never blocks this loop (the lamp keeps working meanwhile).
        while self._ui_calls:
            self.root.after(0, self._ui_calls.pop(0))
        now = time.time()
        owners = self._color_owners(now)
        self._update_master(owners, now)
        role = self._my_role_now()
        if role != self._last_role:
            if self._last_role:
                self._key_event("ping-pong role now {}{}".format(
                    role, " (from N1MM)"))
            self._last_role = role
            self.hello_now.set()  # the other lamps need it at once
        warn = self._master_warning(now)
        if warn != self._master_warn:
            self._key_event("master warning: {}".format(warn or "cleared"))
            self._master_warn = warn
        self._urgent_check(now)
        if self.alert_on and now - self.alert_time > self._my_limit():
            self.alert_on = False  # call time over: end my F12 call
            self.alert_time = now
            self._key_event("F12 call ended after {:g} s".format(self._my_limit()))
            self.hello_now.set()
        active = None
        latest = -1.0
        for station in list(self.stations.values()):
            if (
                station.tx
                and (now - station.last_seen) <= self.stale
                and self._is_tracked(station)
                and station.last_seen > latest
            ):
                latest = station.last_seen
                active = station

        all_calls = self._incoming_calls(now)
        block = self.block_on and any(
            self._block_left(p, now) > 0 for p in all_calls
        )
        if block != self._block_active or bool(all_calls) != self._had_calls:
            self._key_event(
                "incoming call: {} · F-key block: {}{}".format(
                    ", ".join(p.host for p in all_calls) or "none",
                    "ON" if block else "off",
                    "" if self.key_watcher is not None else " (watcher NOT running)",
                )
            )
        self._block_active = block
        self._had_calls = bool(all_calls)
        self._band_busy = self._band_call(now)
        locked, holder = self._pingpong_state(now)
        if locked != self._pp_locked:
            self._key_event("ping-pong lock {}{}".format(
                "ON - slot held by " if locked else "off",
                self.station_name(holder) if locked and holder else "",
            ))
            self.hello_now.set()  # the Overview counts locks
        self._pp_locked, self._pp_holder = locked, holder
        calls = all_calls if active is None else []
        self._show_border(owners, now, all_calls)

        urgent = self._incoming_urgent(now)
        if urgent:
            self._show_urgent(urgent[-1], owners, now)
            self._update_tail()
            return
        self._shown_urgent = None
        if not calls:
            self._shown_call = None
        if calls:
            caller = calls[-1]
            st_ = self._peer_station(caller)
            self._set_who("F12: " + self.peer_name(caller))
            self._freq_text = freq_khz(st_.freq) if st_ is not None else ""
        elif active is not None:
            self._set_who(self.station_name(active))
            self._freq_text = freq_khz(active.freq)
        else:
            self._set_who("")
            self._freq_text = ""
        if calls:
            self._call_sound(calls[-1])
            self._show_call(calls[-1], owners, now)
        elif active is not None:
            color = self._color_for(active, owners, now) or "red"
            shown = active.msg or "—"
            self._set_bg(color)
            if self.canvas.itemcget(self.text_id, "text") != shown:
                self.canvas.itemconfigure(self.text_id, text=shown)
                self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
            role = self.role_label(active)
            self.info.configure(
                text="  ·  ".join(
                    part for part in (
                        self.station_title(active),
                        role,
                        "transmitting",
                        self._band_mode(active),
                    ) if part
                )
            )
        else:
            self._set_bg(COLOR_IDLE)
            idle = "—"
            key, when = self._blocked_key
            if self._pp_locked:
                idle = "BLOCKED · {}".format(key) if key and now - when <= 1.5 else (
                    "WAIT · {} · {} s".format(
                        self.station_name(self._pp_holder) if self._pp_holder else "INB",
                        self._pp_left,
                    )
                )
            elif self.urgent_on:
                idle = "URGENT"
            elif self._go_active(now):
                # RUN has finished: the inband stations may transmit now.
                idle = "GO"
                self._set_bg(GO_COLOR)
                if self._go_beeped != self._go_time:
                    self._go_beeped = self._go_time
                    if self.var_go_sound.get():
                        self._click()
            elif self.alert_on:
                idle = "F12 · {} s".format(self._my_call_left(now))
            if now - self._f12_refused <= 2.0 and self._band_busy is not None:
                idle = "F12 busy · {}".format(self.peer_name(self._band_busy))
            if self.canvas.itemcget(self.text_id, "text") != idle:
                self.canvas.itemconfigure(self.text_id, text=idle)
                self.canvas.itemconfigure(self.text_id, font=self._font_for(idle))
            header = ""
            if self.alert_on:
                header = "F12 call ON ({} MHz, {}) · {} s left – F12 = off  ·  ".format(
                    self._my_band() or "?",
                    "Run call – all stations" if self.alert_run else "S&P stations",
                    self._my_call_left(now),
                ) + header
            if self.urgent_on:
                header = "URGENT call ON ({} {}) – until {} has been sent  ·  ".format(
                    self.urgent_key[0] or "?", self.urgent_key[1], self.urgent_fkey
                ) + header
            note = self._my_color_note(owners)
            if note:
                header = note + "  ·  " + header
            pp_note = self._pingpong_note(now)
            if pp_note:
                header = pp_note + "  ·  " + header
            if self._pp_locked:
                header = "slot taken by {} – wait for RUN (free in {} s at the latest)  ·  ".format(
                    self.station_name(self._pp_holder) if self._pp_holder else "INB",
                    self._pp_left,
                ) + header
            if now - self._f12_refused <= 4.0 and self._band_busy is not None:
                header = "F12 ignored – {} is calling on {} MHz  ·  ".format(
                    self.peer_name(self._band_busy), self._band_busy.alert_band
                ) + header
            if self._status:
                header = self._status + "  ·  " + header
            parts = self._station_rows(now)
            parts.extend(self._peer_rows(now))
            rows = "  ·  ".join(parts) if parts else "waiting for N1MM…"
            self.info.configure(text=header + rows)
        self._update_tail()

    def _update_tail(self):
        warns = [w for w in (self._master_warn, self._port_warning()) if w]
        if warns:
            self.info.configure(
                text="  ·  ".join("⚠ " + w for w in warns) + "  ·  " + self.info.cget("text"),
                fg="#c00000",
            )
        elif self.info.cget("fg") != self._info_fg:
            self.info.configure(fg=self._info_fg)
        self._sync_compact()

    def _test_toggled(self):
        self.test_on = bool(self.var_test.get())
        self._test_write(
            "=== diagnostics {} · PingPong v{} · local IPs {} ===".format(
                "ON" if self.test_on else "OFF",
                __version__,
                ", ".join(sorted(self.local)) or "?",
            )
        )

    def _test_write(self, line):
        try:
            with open(self.test_log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _test_log(self, src, data, via=""):
        """Diagnostics: one line per packet from this PC's N1MM."""
        raw = data.decode("utf-8", errors="replace")
        tag = re.search(r"<(?![?!])([A-Za-z]\w*)", raw)

        def field(name):
            m = re.search(r"<{0}>(.*?)</{0}>".format(name), raw, re.S)
            return m.group(1).strip() if m else "-"

        stamp = time.strftime("%H:%M:%S") + ".{:03d}".format(int(time.time() * 1000) % 1000)
        kind = tag.group(1) if tag else "?"
        if kind == "RadioInfo":
            self._test_write(
                "{}  {}  RadioInfo{}  {}  key='{}'  tx={}  freq={}  mode={}  run={}".format(
                    stamp, src, " ({})".format(via) if via else "",
                    field("StationName"), field("FunctionKeyCaption"),
                    field("IsTransmitting"), field("Freq"), field("Mode"),
                    field("IsRunning"),
                )
            )
        else:
            self._test_write("{}  {}  {}  ({} bytes)".format(stamp, src, kind, len(data)))
        if via or kind.lower() == "spot":
            return
        # Full text of this PC's N1MM packets, to find fields such as a
        # network-master flag. RadioInfo only when something other than
        # transmit state / key caption changed, to keep the log readable.
        flat = re.sub(r">\s+<", "><", raw[raw.find("<"):]).strip()
        if kind == "RadioInfo":
            stable = re.sub(
                r"<(IsTransmitting|FunctionKeyCaption|Timestamp)>.*?</\1>", "", flat, flags=re.S
            )
            if stable == getattr(self, "_last_radio_xml", None):
                return
            self._last_radio_xml = stable
        self._test_write("      XML: " + flat)

    def _start_keys(self):
        if self.key_watcher is not None:
            return True
        watcher = keys.KeyWatcher(
            self._f12_pressed,
            should_block=self._keys_to_block,
            on_blocked=self._key_blocked,
            on_event=self._key_event,
            on_urgent=self._urgent_pressed,
        )
        if not watcher.start():
            messagebox.showerror(
                "PingPong",
                "F12 call is not available:\n{}".format(watcher.error),
                parent=self.root,
            )
            return False
        self.key_watcher = watcher
        return True

    def _keys_toggled(self):
        call, block = bool(self.var_keys.get()), bool(self.var_block.get())
        # The keyboard watcher always runs: the ping-pong lock may need it
        # whenever N1MM is in S&P (role INB).
        if not self._start_keys():
            self.var_keys.set(False)
            self.var_block.set(False)
            call = block = False
        if not call and self.alert_on:
            self.alert_on = False
            self.alert_time = time.time()
            self.hello_now.set()
        self.f12_on, self.block_on = call, block
        if not block:
            self._block_active = False
        self.settings.set("f12_call", call)
        self.settings.set("block_keys", block)

    def _set_pingpong(self, on):
        """Master only: ping-pong lock on/off for every lamp."""
        if not self._i_am_master:
            return
        self.pp_enabled = on
        self.settings.set_text("network", "pingpong", "yes" if on else "no")
        self._key_event("ping-pong lock {} (for all PCs)".format("ON" if on else "OFF"))
        self.hello_now.set()  # tell all lamps right away

    def _click(self):
        """Short soft click (GO), without blocking the window."""
        def beep():
            try:
                import winsound

                winsound.Beep(1400, 40)
            except Exception:
                pass

        threading.Thread(target=beep, daemon=True).start()

    def _relay_toggled(self):
        self.relay_on = bool(self.var_relay.get())
        self.settings.set("share_n1mm", self.relay_on)

    def _key_event(self, text):
        if self.test_on:
            self._test_write("{}  keys: {}".format(time.strftime("%H:%M:%S"), text))

    def _silence_call(self, event=None):
        """Click on a blinking lamp: stop blinking for this call here."""
        if self._shown_urgent is not None:
            peer = self._shown_urgent
            self.silenced.add(("urgent", peer.id, peer.urgent_time))
            self._shown_urgent = None
            return
        peer = self._shown_call
        if peer is not None:
            self.silenced.add((peer.id, peer.alert_time))
            self._shown_call = None

    def _call_sound(self, peer):
        """Double beep once when a call starts blinking on this lamp."""
        key = (peer.id, peer.alert_time)
        if key in self._beeped:
            return
        self._beeped.add(key)
        if len(self._beeped) > 50:
            self._beeped = {key}
        if not self.var_sound.get():
            return
        self._key_event("call sound for {}".format(self.peer_name(peer)))

        def beep():
            try:
                import winsound

                for _ in range(2):
                    winsound.Beep(1000, 150)
                    time.sleep(0.08)
            except Exception:
                pass  # no sound device / not Windows

        threading.Thread(target=beep, daemon=True).start()

    def _show_call(self, peer, owners, now):
        self._shown_call = peer
        station = self._peer_station(peer)
        color = self._call_color(peer, owners, now)
        name = self.peer_name(peer) if peer.alias or station is None else self.station_name(station)
        lit = int(now / BLINK_HALF) % 2 == 0
        bg = color if lit else COLOR_IDLE
        left = max(0, int(self._block_left(peer, now) + 0.999))
        shown = "F12 · {} · {} s".format(name, left)
        key, when = self._blocked_key
        if self._block_active and key and now - when <= 1.5:
            shown = "BLOCKED · {}".format(key)
        if now - self._f12_refused <= 1.5:
            shown = "BLOCKED · F12"
        self._set_bg(bg)
        if self.canvas.itemcget(self.text_id, "text") != shown:
            self.canvas.itemconfigure(self.text_id, text=shown)
            self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
        self.info.configure(
            text="{} calls (F12)  ·  {} MHz  ·  {} s left  ·  {}click = silence".format(
                self.peer_name(peer),
                peer.alert_band,
                left,
                self._block_note(peer, now),
            )
        )

    def _block_note(self, peer, now):
        if not self.block_on:
            return ""
        left = self._block_left(peer, now)
        if left > 0 and self._block_active:
            return "F-keys blocked  ·  "
        return ""

    def _call_color(self, peer, owners, now):
        station = self._peer_station(peer)
        if peer.color and owners.get(peer.color.lower()) is peer:
            return peer.color
        if station is not None:
            return self._color_for(station, owners, now) or "red"
        return "red"

    def _show_border(self, owners, now, calls):
        """Wide border for F12 calls (same width always, so the window
        never changes size; invisible when there is no call).

        - my own call on: border in my color, blinking;
        - a call received here: border in the caller's color, steady;
          while my F-keys are blocked (F12 lock) the lamp and the border
          swap colors back and forth - lamp lit / border dark, then lamp
          dark / border lit.
        """
        if self.urgent_on:
            mine = self._local_station()
            color = (self._color_for(mine, owners, now) if mine else "") or "red"
            lit = int(now / URGENT_HALF) % 2 == 0
            want = color if lit else "white"
        elif self.alert_on:
            mine = self._local_station()
            color = (self._color_for(mine, owners, now) if mine else "") or "red"
            lit = int(now / BLINK_HALF) % 2 == 0
            want = color if lit else self._window_bg
        elif calls:
            want = self._call_color(calls[-1], owners, now)
            if self._block_active:
                lit = int(now / BLINK_HALF) % 2 == 0
                want = COLOR_IDLE if lit else want  # opposite of the lamp
        else:
            want = self._window_bg
        if self.canvas.cget("highlightbackground") != want:
            self.canvas.configure(highlightbackground=want, highlightcolor=want)

    def on_close(self):
        try:
            self._save_window()
        except Exception:
            pass
        if self.key_watcher is not None:
            self.key_watcher.stop()
        self.stop.set()
        self.root.destroy()


def on_screen(x, y):
    """Is the point on one of the monitors connected right now?"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        return bool(user32.MonitorFromPoint(wintypes.POINT(x, y), 0))  # NULL if none
    except Exception:
        return True

_instance_mutex = None  # Windows mutex: one lamp per PC


def claim_single_instance(wait):
    """Only one lamp per PC. Waits up to `wait` s for an old one to exit
    (after an update the old lamp closes a moment after starting us)."""
    global _instance_mutex
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    deadline = time.time() + wait
    while True:
        handle = kernel32.CreateMutexW(None, False, "Local\\PingPongLamp")
        if handle and ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _instance_mutex = handle  # held until this process exits
            return True
        if handle:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        if time.time() >= deadline:
            return False
        time.sleep(0.25)

def main():
    parser = argparse.ArgumentParser(description="N1MM UDP 12060 display")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--stale", type=float, default=DEFAULT_STALE)
    parser.add_argument("--config", default=os.path.join(app_dir(), "lamps.cfg"))
    parser.add_argument(
        "--discovery-port",
        type=int,
        default=DEFAULT_DISCOVERY_PORT,
        help="UDP port for PingPong-to-PingPong discovery (0 = off)",
    )
    parser.add_argument(
        "--no-discovery", action="store_true", help="disable active discovery"
    )
    args = parser.parse_args()

    after_update = os.environ.pop("PINGPONG_AFTER_UPDATE", "") == "1"
    if not claim_single_instance(15.0 if after_update else 3.0):
        hidden = tk.Tk()
        hidden.withdraw()
        messagebox.showinfo(
            "PingPong",
            "PingPong is already running on this PC.\n\n"
            "Only one lamp per PC is allowed - use the window that is open.",
            parent=hidden,
        )
        hidden.destroy()
        return

    root = tk.Tk()
    discovery_port = 0 if args.no_discovery else args.discovery_port
    DisplayApp(root, args.config, args.port, args.stale, discovery_port)
    root.mainloop()

if __name__ == "__main__":
    main()
