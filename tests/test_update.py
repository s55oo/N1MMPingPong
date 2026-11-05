"""Update downloads are checked by SHA-256 (GitHub digest and SHA256SUMS.txt)."""

import hashlib
import os
import pathlib
import tempfile
import unittest

from helpers import m  # noqa: F401  (puts the project on sys.path)

import pingpong_update as upd


def url(path):
    return pathlib.Path(path).as_uri()


class SumsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe = os.path.join(self.dir, "src.exe")
        with open(self.exe, "wb") as fh:
            fh.write(b"MZ" + b"x" * 1000)
        self.good = hashlib.sha256(open(self.exe, "rb").read()).hexdigest()
        self.target = os.path.join(self.dir, "n1mm_lamps.exe")

    def sums(self, digest):
        path = os.path.join(self.dir, "SHA256SUMS.txt")
        with open(path, "w") as fh:
            fh.write("{} *n1mm_lamps.exe\n{} *pingpong_overview.exe\n".format(digest, "0" * 64))
        return url(path)

    def release(self, sha256="", sums_url=""):
        return {"version": "9.9", "tag": "v9.9", "name": "n1mm_lamps.exe", "url": url(self.exe),
                "size": os.path.getsize(self.exe), "sha256": sha256, "sums_url": sums_url,
                "date": ""}

    def test_parse_sums(self):
        sums = upd.parse_sums("{} *n1mm_lamps.exe\n{}  pingpong_overview.exe\njunk\n".format(
            "A" * 64, "b" * 64))
        self.assertEqual(sums, {"n1mm_lamps.exe": "a" * 64, "pingpong_overview.exe": "b" * 64})

    def test_download_ok_with_sums_file(self):
        part = upd.download(self.release(sums_url=self.sums(self.good)), self.target)
        self.assertTrue(os.path.exists(part))

    def test_wrong_sum_in_file_refused(self):
        with self.assertRaises(ValueError):
            upd.download(self.release(sums_url=self.sums("1" * 64)), self.target)
        self.assertFalse(os.path.exists(self.target + ".download"))

    def test_github_and_file_must_agree(self):
        with self.assertRaises(ValueError):
            upd.download(self.release(sha256=self.good, sums_url=self.sums("2" * 64)), self.target)

    def test_github_digest_alone(self):
        upd.download(self.release(sha256=self.good), self.target)
        with self.assertRaises(ValueError):
            upd.download(self.release(sha256="3" * 64), self.target)

    def test_unreachable_sums_file_falls_back_to_github_digest(self):
        rel = self.release(sha256=self.good, sums_url=url(os.path.join(self.dir, "missing.txt")))
        upd.download(rel, self.target)


if __name__ == "__main__":
    unittest.main()
