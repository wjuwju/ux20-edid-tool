import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from ux20_edid import INTERNAL_EDID, UX20_INTERFACE, validate_profile, write_profile


def profile(tag):
    data = bytearray(256)
    data[:8] = bytes.fromhex("00 ff ff ff ff ff ff 00")
    data[16] = tag
    data[126] = 1
    data[128:132] = bytes((2, 3, 4, 0))
    for end in (127, 255):
        data[end] = -sum(data[end - 127:end]) % 256
    return bytes(data)


class FakeUX20:
    def __init__(self, mode=INTERNAL_EDID):
        self.operations = []
        self.edid = profile(1)
        self.edid_mode = mode
        self.backup = None

    def read_edid(self):
        return self.edid

    def mode(self):
        return self.edid_mode

    def write_block(self, index, data):
        if not self.backup or self.backup.read_bytes() != self.edid:
            raise AssertionError("Missing write-before backup")
        self.operations.append((index, data))

    def apply_internal_mode(self):
        self.operations.append("internal")


class EDIDTests(unittest.TestCase):
    def test_validate_profile(self):
        source = profile(1)
        self.assertEqual(validate_profile(source), source)
        for bad in (source[:128], source + b"\0", source[:-1] + b"\0"):
            with self.assertRaises(ValueError):
                validate_profile(bad)

    def test_interface_filter(self):
        suffix = rb"&MI_07#7&x&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"
        device = rb"\\?\HID#VID_048D&PID_9337" + suffix
        self.assertEqual(UX20_INTERFACE.findall(b"Interface Path: " + device), [device])
        self.assertFalse(UX20_INTERFACE.search(device.replace(b"9337", b"9339")))

    def test_write_order_and_backup(self):
        device = FakeUX20()
        wanted = profile(2)
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            device.backup = backup
            with contextlib.redirect_stdout(io.StringIO()):
                write_profile(device, wanted, backup)
            self.assertEqual(backup.read_bytes(), device.edid)
            self.assertEqual(device.operations, [(0, wanted[:128]), (1, wanted[128:]), "internal"])

    def test_existing_backup_and_wrong_mode_prevent_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.bin"
            device = FakeUX20(mode=0)
            with self.assertRaises(ValueError):
                write_profile(device, profile(2), backup)
            self.assertFalse(backup.exists())
            self.assertFalse(device.operations)

            device = FakeUX20()
            backup.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                write_profile(device, profile(2), backup)
            self.assertEqual(backup.read_bytes(), b"keep")
            self.assertFalse(device.operations)


if __name__ == "__main__":
    unittest.main()

