from __future__ import annotations


def u24be(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError("value does not fit in 24 bits")
    return value.to_bytes(4, "big")[1:]


def u16be(value: int) -> bytes:
    return value.to_bytes(2, "big")


def u32be(value: int) -> bytes:
    return value.to_bytes(4, "big")


def test_unit_ready_cdb() -> bytes:
    return bytes([0x00, 0, 0, 0, 0, 0])


def request_sense_cdb(allocation: int = 14) -> bytes:
    return bytes([0x03, 0, 0, 0, allocation & 0xFF, 0])


def inquiry_cdb(*, evpd: bool = False, page: int = 0, allocation: int = 0x60) -> bytes:
    return bytes([0x12, 0x01 if evpd else 0x00, page & 0xFF, 0, allocation & 0xFF, 0])


def reserve_unit_cdb() -> bytes:
    return bytes([0x16, 0, 0, 0, 0, 0])


def release_unit_cdb() -> bytes:
    return bytes([0x17, 0, 0, 0, 0, 0])


def scan_cdb(window_count: int) -> bytes:
    return bytes([0x1B, 0, 0, 0, window_count & 0xFF, 0])


def scan_payload(*, duplex: bool = False, back_only: bool = False) -> bytes:
    if duplex:
        return bytes([0x00, 0x01])
    if back_only:
        return bytes([0x01])
    return bytes([0x00])


def set_window_cdb(payload_len: int = 0x34) -> bytes:
    return bytes([0x24, 0, 0, 0, 0, 0]) + u24be(payload_len) + bytes([0])


def get_window_cdb(payload_len: int = 0x34) -> bytes:
    cdb = bytearray(10)
    cdb[0] = 0x25
    cdb[6:9] = u24be(payload_len)
    return bytes(cdb)


def set_window_payload(
    *,
    window_id: int = 0,
    dpi_x: int = 300,
    dpi_y: int = 300,
    ulx_1200: int = 0,
    uly_1200: int = 0,
    width_1200: int = 2550 * 1200 // 300,
    height_1200: int = 3300 * 1200 // 300,
    composition: int = 2,
    bits_per_pixel: int = 8,
    brightness: int = 0,
    threshold: int = 0,
    contrast: int = 0,
    reverse_padding: int = 0x10,
    compression: int = 0x80,
    compression_arg: int = 3,
    vendor_unique_2a: int = 0,
) -> bytes:
    payload = bytearray(0x34)
    payload[6:8] = u16be(0x2C)

    desc = memoryview(payload)[8:]
    desc[0] = window_id & 0xFF
    desc[2:4] = u16be(dpi_x)
    desc[4:6] = u16be(dpi_y)
    desc[6:10] = u32be(ulx_1200)
    desc[10:14] = u32be(uly_1200)
    desc[14:18] = u32be(width_1200)
    desc[18:22] = u32be(height_1200)
    desc[0x16] = brightness & 0xFF
    desc[0x17] = threshold & 0xFF
    desc[0x18] = contrast & 0xFF
    desc[0x19] = composition & 0xFF
    desc[0x1A] = bits_per_pixel & 0xFF
    desc[0x1D] = reverse_padding & 0xFF
    desc[0x20] = compression & 0xFF
    desc[0x21] = compression_arg & 0xFF
    desc[0x2A] = vendor_unique_2a & 0xFF
    return bytes(payload)


def read_cdb(*, data_type: int = 0x00, uid: int = 0, lid: int = 0, length: int = 0x10000) -> bytes:
    return bytes([0x28, 0, data_type & 0xFF, 0, uid & 0xFF, lid & 0xFF]) + u24be(length) + bytes([0])


READ_KIND_FIELDS: tuple[tuple[int, int, int], ...] = (
    (0x00, 0x00, 0x00),
    (0x80, 0x00, 0x00),
    (0x80, 0x00, 0x04),
    (0x80, 0x00, 0x01),
    (0x84, 0x00, 0x00),
    (0x8B, 0x00, 0x00),
    (0x8C, 0x00, 0x00),
    (0x8C, 0x00, 0x01),
    (0xA1, 0x00, 0x00),
    (0x91, 0x07, 0x00),
    (0x91, 0x09, 0x00),
    (0x91, 0x0A, 0x00),
    (0x91, 0x0C, 0x00),
    (0x91, 0x23, 0x00),
    (0x91, 0x25, 0x00),
    (0x91, 0x26, 0x00),
    (0x00, 0x00, 0x00),
    (0xAA, 0x00, 0x00),
)


def read_kind_cdb(kind: int, *, length: int) -> bytes:
    try:
        data_type, uid, lid = READ_KIND_FIELDS[kind]
    except IndexError as exc:
        raise ValueError("driver read kind must be in range 0..17") from exc
    return read_cdb(data_type=data_type, uid=uid, lid=lid, length=length)


def send_cdb(*, data_type: int, selector: int = 0, length: int) -> bytes:
    return (
        bytes([0x2A, 0, data_type & 0xFF, 0])
        + u16be(selector)
        + u24be(length)
        + bytes([0])
    )


def set_adjust_data_cdb(*, version: int = 3, payload_len: int = 0x28) -> bytes:
    return bytes([0xE1, 0, 0, 0, 0, version & 0xFF]) + u24be(payload_len) + bytes([0])


def set_adjust_data_payload_v3(
    *,
    front_gain: tuple[int, int, int] = (0, 0, 0),
    front_offset: tuple[int, int, int] = (0, 0, 0),
    front_exposure: tuple[int, int, int] = (0, 0, 0),
    back_gain: tuple[int, int, int] = (0, 0, 0),
    back_offset: tuple[int, int, int] = (0, 0, 0),
    back_exposure: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    payload = bytearray(0x28)
    payload[0x00:0x03] = bytes(value & 0xFF for value in front_gain)
    payload[0x04:0x07] = bytes(value & 0xFF for value in front_offset)
    payload[0x08:0x0A] = u16be(front_exposure[0])
    payload[0x0A:0x0C] = u16be(front_exposure[1])
    payload[0x0C:0x0E] = u16be(front_exposure[2])
    payload[0x14:0x17] = bytes(value & 0xFF for value in back_gain)
    payload[0x18:0x1B] = bytes(value & 0xFF for value in back_offset)
    payload[0x1C:0x1E] = u16be(back_exposure[0])
    payload[0x1E:0x20] = u16be(back_exposure[1])
    payload[0x20:0x22] = u16be(back_exposure[2])
    return bytes(payload)


OBJECT_POSITION_ACTION_BYTE = {
    0: 0x00,
    1: 0x01,
    2: 0x04,
}


def object_position_action_cdb(action: int) -> bytes:
    try:
        action_byte = OBJECT_POSITION_ACTION_BYTE[action]
    except KeyError as exc:
        raise ValueError("object position action must be 0, 1, or 2") from exc
    return bytes([0x31, action_byte, 0, 0, 0, 0, 0, 0, 0, 0])


def object_position_cdb(*, feed: bool) -> bytes:
    return object_position_action_cdb(1 if feed else 0)


def get_memory_cdb(offset: int, length: int) -> bytes:
    if length > 0x2000:
        raise ValueError("Canon driver chunks GET MEMORY at 0x2000 bytes")
    cdb = bytearray(10)
    cdb[0] = 0x3B
    cdb[2:6] = u32be(offset)
    cdb[7:9] = u16be(length)
    return bytes(cdb)


def get_scanner_status_cdb(length: int = 8) -> bytes:
    cdb = bytearray(12)
    cdb[0] = 0xC5
    cdb[4:8] = length.to_bytes(4, "little")
    return bytes(cdb)


def define_scan_mode_cdb(payload_len: int = 0x14) -> bytes:
    return bytes([0xD6, 0x10, 0, 0, payload_len & 0xFF, 0])


def define_scan_mode_feed_payload(
    *,
    param_04: bool = False,
    param_05: bool = False,
    param_06: bool = False,
) -> bytes:
    """Build page 0x30 exactly as the Canon driver maps SScanModeParam mode 0."""
    payload = bytearray(0x14)
    payload[4] = 0x30
    payload[5] = 0x0E
    if param_04 and param_05:
        payload[7] = 0x05
    elif param_04:
        payload[7] = 0x01
    elif param_05:
        payload[7] = 0x04
    if param_06:
        payload[9] = 0x10
    return bytes(payload)


def define_scan_mode_buffer_payload(
    *,
    duplex: bool = False,
    async_buffer: bool = False,
    source_mode: int | None = None,
    flag_05: bool = False,
    flag_06: bool = False,
    flag_0a: bool = False,
    interval: int = 0,
) -> bytes:
    """Build page 0x32 as the Canon driver maps SScanModeParam mode 1.

    The names are deliberately conservative. The driver copies SScanModeParam
    bytes 4, 5, 6, 8..9, and 10 into this page, but the semantic names for every
    bit are not fully confirmed. ``duplex`` keeps backward-compatible behavior
    by selecting source_mode=2 when no explicit source_mode is supplied.
    """
    payload = bytearray(0x14)
    payload[4] = 0x32
    payload[5] = 0x0E
    payload[6] = (0x02 if duplex and source_mode is None else (source_mode or 0)) & 0xFF
    payload[7] = 0x01
    if async_buffer or flag_05:
        payload[0x0A] |= 0x40
    if flag_06:
        payload[0x0A] |= 0x20
    if flag_0a:
        payload[0x0A] |= 0x08
    payload[0x0C:0x0E] = u16be(interval)
    return bytes(payload)


def define_scan_mode_color_payload(
    *,
    byte_0b: int = 0,
    byte_0c: int = 0,
    byte_0d: int = 0,
    byte_0e: int = 0,
    byte_11: int = 0,
    byte_12: int = 0,
) -> bytes:
    """Build page 0x36 as the Canon driver maps SScanModeParam mode 2."""
    payload = bytearray(0x14)
    payload[4] = 0x36
    payload[5] = 0x0E
    payload[0x0B] = byte_0b & 0xFF
    payload[0x0C] = byte_0c & 0xFF
    payload[0x0D] = byte_0d & 0xFF
    payload[0x0E] = byte_0e & 0xFF
    payload[0x11] = byte_11 & 0xFF
    payload[0x12] = byte_12 & 0xFF
    return bytes(payload)


def cancel_cdb() -> bytes:
    return bytes([0xD8, 0, 0, 0, 0, 0])
