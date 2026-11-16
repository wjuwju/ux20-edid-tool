"""Read and write the HDMI EDID of a JYJS UX20 HDR (048D:9337) on Windows."""

import argparse
import ctypes
from ctypes import wintypes
from hashlib import sha256
from pathlib import Path
import re
import subprocess
import sys


HID_CLASS = "{4d1e55b2-f16f-11cf-88cb-001111000030}"
UX20_INTERFACE = re.compile(
    rb"\\\\\?\\HID#VID_048D&PID_9337&MI_07#[^#\s]+#" + re.escape(HID_CLASS.encode()),
    re.IGNORECASE,
)
BLOCK_LENGTH = 128
EDID_LENGTH = 2 * BLOCK_LENGTH
EDID_HEADER = bytes.fromhex("00 ff ff ff ff ff ff 00")
EDID_BLOCK_ADDRESSES = (0x10, 0x11)
EDID_MODE_ADDRESS = 0x20
INTERNAL_EDID = 2


def validate_profile(profile):
    if len(profile) != EDID_LENGTH or not profile.startswith(EDID_HEADER):
        raise ValueError("Expected a 256-byte EDID with a valid base header.")
    if profile[126] != 1:
        raise ValueError("Expected one EDID extension block.")
    for index, offset in enumerate(range(0, EDID_LENGTH, BLOCK_LENGTH), start=1):
        if sum(profile[offset:offset + BLOCK_LENGTH]) % 256:
            raise ValueError(f"EDID block {index} has an invalid checksum.")
    return profile


def find_interface():
    result = subprocess.run(
        ["pnputil.exe", "/enum-interfaces", "/enabled", "/class", HID_CLASS],
        capture_output=True,
        check=True,
        timeout=10,
    )
    matches = {match.decode("ascii").lower() for match in UX20_INTERFACE.findall(result.stdout)}
    if not matches:
        raise OSError("No enabled UX20 HID interface (048D:9337, MI_07) was found.")
    if len(matches) != 1:
        raise OSError("Multiple UX20 devices found. Connect only the one you intend to use.")
    return matches.pop()


class UX20:
    def __init__(self, interface, writable=False):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hid = ctypes.WinDLL("hid", use_last_error=True)
        self.kernel.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        for name in ("HidD_SetOutputReport", "HidD_GetInputReport"):
            api = getattr(self.hid, name)
            api.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG)
            api.restype = wintypes.BOOLEAN

        # Read commands also send a non-persistent Output Report to select their address.
        self.handle = self.kernel.CreateFileW(interface, 0xC0000000, 3, None, 3, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.writable = writable

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.kernel.CloseHandle(self.handle)

    def _input(self, length):
        reply = ctypes.create_string_buffer(length)
        reply[0] = 5
        if not self.hid.HidD_GetInputReport(self.handle, reply, length):
            raise ctypes.WinError(ctypes.get_last_error())
        return reply.raw

    def _request(self, group, address, payload, length_field, reply_length):
        frame = bytes((6, length_field, group, address, len(payload))) + payload
        request = ctypes.create_string_buffer(frame, 255)
        if not self.hid.HidD_SetOutputReport(self.handle, request, len(request)):
            raise ctypes.WinError(ctypes.get_last_error())
        return self._input(reply_length)

    def _read_setting(self, address, length):
        return self._request(7, address, bytes((length,)), 6, 0x7FF)

    def _write_setting(self, address, value):
        if not self.writable:
            raise OSError("This session was opened for reads only.")
        result = self._request(6, address, value, len(value) + 4, 0x6FF)
        if result[0] != 1:
            raise OSError(f"Device rejected address 0x{address:02X} (status 0x{result[0]:02X}).")

    def read_edid(self):
        blocks = [self._read_setting(address, BLOCK_LENGTH)[:BLOCK_LENGTH]
                  for address in EDID_BLOCK_ADDRESSES]
        return validate_profile(b"".join(blocks))

    def mode(self):
        return self._read_setting(EDID_MODE_ADDRESS, 1)[0]

    def write_block(self, index, block):
        self._write_setting(EDID_BLOCK_ADDRESSES[index], block)

    def apply_internal_mode(self):
        self._write_setting(EDID_MODE_ADDRESS, bytes((INTERNAL_EDID,)))

    def signal(self):
        data = self._input(0xBFF)
        width = int.from_bytes(data[4:6], "big")
        height = int.from_bytes(data[2:4], "big") * (2 if data[8] else 1)
        return width, height, data[7], data[6], bool(data[9])


def save_new(path, data):
    with path.open("xb") as output:
        output.write(data)


def write_profile(device, profile, backup_path):
    original = device.read_edid()
    if device.mode() != INTERNAL_EDID:
        raise ValueError("The device must already be in Internal EDID mode.")
    if original == profile:
        print(f"Device already reports the requested EDID (SHA-256 {sha256(profile).hexdigest()}).")
        return

    save_new(backup_path, original)
    print(f"Backup saved: {backup_path} (SHA-256 {sha256(original).hexdigest()})", flush=True)
    try:
        for index, offset in enumerate(range(0, EDID_LENGTH, BLOCK_LENGTH)):
            device.write_block(index, profile[offset:offset + BLOCK_LENGTH])
            print(f"Device acknowledged block {index + 1}.", flush=True)
        device.apply_internal_mode()
        print("Device acknowledged Internal EDID mode.", flush=True)
    except OSError as error:
        raise OSError(f"Write may be partial. Check the device with verify. Backup: {backup_path}. {error}") from error

    print("Disconnect USB and all HDMI cables for about 10 seconds, then reconnect USB first.")
    print("Run verify with the same profile after the device returns.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("read", "Save the device EDID to a new file"),
        ("verify", "Compare the device EDID to a file"),
        ("write", "Back up and write a new EDID"),
        ("signal", "Show the current HDMI input timing"),
    ):
        command = commands.add_parser(name, help=help_text)
        if name != "signal":
            command.add_argument("file", type=Path)
        if name == "write":
            command.add_argument("--backup", type=Path, required=True, help="New path for the original EDID.")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.exit(1, "UX20 HID access is supported on Windows only.\n")

    try:
        expected = validate_profile(args.file.read_bytes()) if args.command in {"verify", "write"} else None
        with UX20(find_interface(), writable=args.command == "write") as device:
            if args.command == "read":
                data = device.read_edid()
                save_new(args.file, data)
                print(f"Saved {args.file} (SHA-256 {sha256(data).hexdigest()}).")
            elif args.command == "verify":
                data = device.read_edid()
                print(f"Device EDID SHA-256: {sha256(data).hexdigest()}")
                if data != expected:
                    print("Device EDID differs from the supplied profile.", file=sys.stderr)
                    return 2
                print("All 256 bytes match.")
            elif args.command == "write":
                write_profile(device, expected, args.backup)
            else:
                width, height, rate, depth, hdr = device.signal()
                if not width or not height:
                    print("No HDMI signal reported.")
                else:
                    print(f"{width}x{height} @ {rate} Hz, {depth}-bit, {'HDR' if hdr else 'SDR'}")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

