"""
USBTMC MCP Server

This MCP server provides a unified interface for communicating with multiple
measurement instruments simultaneously via the USBTMC protocol.
"""

import logging
import time
from typing import Any, Optional

import libusb_package
from mcp.server.fastmcp import FastMCP, Image
from pydantic import BaseModel, ConfigDict, Field

from usbtmc_lite import USBTMC
from usbtmc_lite.utils import (
    find_firmware_mode_devices,
    find_usbtmc_devices,
    unlock_keysight_device,
)

# ============================================================
# Constants
# ============================================================
MAX_DEVICES = 16
DEFAULT_WAIT_TIME = 0.1
DEFAULT_MAX_BYTES = 1024

# ============================================================
# Logging Setup
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# Global State
# ============================================================
backend = libusb_package.get_libusb1_backend()
device_pool: dict[int, dict[str, Any]] = {}  # {device_id: {"instance": USBTMC, "info": dict}}

# ============================================================
# MCP Server Setup
# ============================================================
mcp = FastMCP(
    name="usbtmc_mcp",
    instructions="""
USBTMC Multi-Device Controller
- Supports up to 16 simultaneous device connections
- Each device gets a unique device_id (0-15) after connection
- Workflow: list_devices → connect → use device_id for operations → disconnect
- Low-level SCPI command interface: AI constructs appropriate commands
""",
)


# ============================================================
# Pydantic Input Models
# ============================================================
class DeviceIdInput(BaseModel):
    """Base input model for operations requiring a device_id."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    device_id: int = Field(
        ...,
        description="Unique identifier for the device connection (0-15), returned by usbtmc_connect()",
        ge=0,
        lt=MAX_DEVICES,
    )


class ConnectInput(BaseModel):
    """Input model for connecting to a USBTMC device."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    manufacturer: Optional[str] = Field(
        default=None,
        description="Device manufacturer name to match (e.g., 'Keysight Technologies'). If None, not filtered.",
        max_length=256,
    )
    product: Optional[str] = Field(
        default=None,
        description="Device product name to match (e.g., 'DSO-X 3024A'). If None, not filtered.",
        max_length=256,
    )
    serial_number: Optional[str] = Field(
        default=None,
        description="Unique device serial number. If provided, only this specific device is selected.",
        max_length=256,
    )


class SendInput(DeviceIdInput):
    """Input model for sending commands to a device."""

    command: str = Field(
        ...,
        description="SCPI command string to send (e.g., '*IDN?', ':MEASure:FREQuency?')",
        min_length=1,
        max_length=4096,
    )


class ReceiveInput(DeviceIdInput):
    """Input model for receiving data from a device."""

    max_bytes: int = Field(
        default=DEFAULT_MAX_BYTES,
        description="Maximum bytes to read from the device buffer",
        ge=1,
        le=1024 * 1024,
    )


class QueryInput(DeviceIdInput):
    """Input model for query operations (send + receive)."""

    command: str = Field(
        ...,
        description="SCPI command string to send (e.g., '*IDN?', ':MEASure:FREQuency?')",
        min_length=1,
        max_length=4096,
    )
    wait_time: float = Field(
        default=DEFAULT_WAIT_TIME,
        description=(
            "Time to wait between send and receive in seconds. "
            "Use 0.5-2.0s after changing settings, 0.1s (default) for simple queries."
        ),
        ge=0.0,
        le=30.0,
    )
    max_bytes: int = Field(
        default=DEFAULT_MAX_BYTES,
        description="Maximum bytes to read from the response",
        ge=1,
        le=1024 * 1024,
    )


# ============================================================
# Helper Functions
# ============================================================
def _get_device(device_id: int) -> tuple[USBTMC, dict[str, str]]:
    """
    Get a device instance and info by device_id.

    Args:
        device_id: Device identifier

    Returns:
        Tuple of (USBTMC instance, device info dict)

    Raises:
        ValueError: If device_id is not found in the pool
    """
    if device_id not in device_pool:
        connected = list(device_pool.keys())
        raise ValueError(
            f"Invalid device_id: {device_id}. "
            f"Connected devices: {connected if connected else 'none'}. "
            "Use usbtmc_list_connected_devices() to see active connections."
        )
    dev_data = device_pool[device_id]
    return dev_data["instance"], dev_data["info"]


def _get_available_device_id() -> int:
    """
    Find the lowest available device_id in the range 0-15.

    Returns:
        Available device_id (0-15), or -1 if pool is full
    """
    for i in range(MAX_DEVICES):
        if i not in device_pool:
            return i
    return -1


def _format_device_info(device) -> dict[str, str]:
    """Extract device information as a dictionary."""
    return {
        "manufacturer": device.manufacturer or "Unknown",
        "product": device.product or "Unknown",
        "serial_number": device.serial_number or "Unknown",
    }


# ============================================================
# MCP Tools - Device Discovery
# ============================================================
@mcp.tool(
    name="usbtmc_unlock_keysight_devices",
    annotations={
        "title": "Unlock Keysight Firmware Mode Devices",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def usbtmc_unlock_keysight_devices() -> dict[str, Any]:
    """
    Switch all Keysight devices from firmware update mode to USBTMC mode.

    Some Keysight USB modular instruments (e.g., U2700 series) power on
    in firmware update mode and require a vendor-specific command to
    switch to normal USBTMC operation.

    After calling this function, devices will re-enumerate on the USB bus.
    Wait a moment, then call usbtmc_list_devices() to discover them.

    Returns:
        dict: Result containing:
            - unlocked_count (int): Number of successfully unlocked devices
            - total_found (int): Total devices found in firmware mode
            - message (str): Success or informational message
            - errors (list[str], optional): List of error messages if any device failed
    """
    devices = find_firmware_mode_devices(backend=backend)

    if not devices:
        return {
            "unlocked_count": 0,
            "total_found": 0,
            "message": "No devices found in firmware update mode",
        }

    unlocked_count = 0
    errors = []

    for dev in devices:
        try:
            unlock_keysight_device(dev)
            unlocked_count += 1
        except Exception as e:
            error_msg = f"Failed to unlock device: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    result: dict[str, Any] = {
        "unlocked_count": unlocked_count,
        "total_found": len(devices),
        "message": f"Unlocked {unlocked_count}/{len(devices)} device(s). "
        "Wait a moment for USB re-enumeration, then call usbtmc_list_devices().",
    }

    if errors:
        result["errors"] = errors

    return result


@mcp.tool(
    name="usbtmc_list_devices",
    annotations={
        "title": "List Available USBTMC Devices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def usbtmc_list_devices() -> dict[str, Any]:
    """
    List all available USBTMC devices connected via USB.

    This is typically the first step in the workflow:
    1. usbtmc_list_devices() - Discover available devices
    2. usbtmc_connect() - Connect to a device and get device_id
    3. Use device_id for operations (send/query/etc.)
    4. usbtmc_disconnect() - Close connection when done

    Returns:
        dict: Result containing:
            - count (int): Number of devices found
            - devices (list[dict]): List of device info dictionaries, each containing:
                - manufacturer (str): Device manufacturer name
                - product (str): Device product/model name
                - serial_number (str): Unique device serial number

    Example:
        >>> result = usbtmc_list_devices()
        >>> result
        {
            "count": 1,
            "devices": [
                {
                    "manufacturer": "Keysight Technologies",
                    "product": "DSO-X 3024A",
                    "serial_number": "MY12345678"
                }
            ]
        }
    """
    devices = find_usbtmc_devices(backend=backend)

    result = []
    for device in devices:
        try:
            result.append(_format_device_info(device))
        except Exception as e:
            logger.warning(f"Could not read device info: {e}")

    logger.info(f"Found {len(result)} USBTMC devices")
    return {"count": len(result), "devices": result}


@mcp.tool(
    name="usbtmc_list_connected_devices",
    annotations={
        "title": "List Connected USBTMC Devices",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def usbtmc_list_connected_devices() -> dict[str, Any]:
    """
    List all currently connected (active) USBTMC devices.

    Returns:
        dict: Result containing:
            - count (int): Number of connected devices
            - devices (list[dict]): List of connected device info, each containing:
                - device_id (int): Unique identifier for the connection
                - manufacturer (str): Device manufacturer name
                - product (str): Device product/model name
                - serial_number (str): Device serial number

    Example:
        >>> result = usbtmc_list_connected_devices()
        >>> result
        {
            "count": 1,
            "devices": [
                {
                    "device_id": 0,
                    "manufacturer": "Keysight Technologies",
                    "product": "DSO-X 3024A",
                    "serial_number": "MY12345678"
                }
            ]
        }
    """
    result = []
    for device_id, dev_data in device_pool.items():
        info = dev_data["info"]
        result.append(
            {
                "device_id": device_id,
                "manufacturer": info["manufacturer"],
                "product": info["product"],
                "serial_number": info["serial_number"],
            }
        )

    logger.info(f"Currently connected to {len(result)} devices")
    return {"count": len(result), "devices": result}


# ============================================================
# MCP Tools - Connection Management
# ============================================================
@mcp.tool(
    name="usbtmc_connect",
    annotations={
        "title": "Connect to USBTMC Device",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def usbtmc_connect(params: ConnectInput) -> dict[str, Any]:
    """
    Connect to a USBTMC device and receive a device_id for subsequent operations.

    The device_id is used to identify the connection in all subsequent operations.
    If multiple devices match the criteria, the connection will fail - provide more
    specific criteria to uniquely identify the target device.

    Args:
        params (ConnectInput): Connection parameters containing:
            - manufacturer (str, optional): Device manufacturer name to match
            - product (str, optional): Device product name to match
            - serial_number (str, optional): Unique device serial number (most specific)

    Returns:
        dict: Result containing on success:
            - device_id (int): Unique identifier for this connection (use for all subsequent operations)
            - manufacturer (str): Device manufacturer name
            - product (str): Device product/model name
            - serial_number (str): Device serial number
            - message (str): Success message

        On error:
            - error (str): Error message describing the failure

    Note:
        Maximum 16 simultaneous connections are supported.
    """
    global device_pool

    # Check if pool is full
    device_id = _get_available_device_id()
    if device_id == -1:
        error_msg = (
            f"Maximum device limit reached ({MAX_DEVICES} devices). "
            "Disconnect a device first using usbtmc_disconnect()."
        )
        logger.error(error_msg)
        return {"error": error_msg}

    # Find matching devices
    devices = find_usbtmc_devices(backend=backend)

    matched = []
    for dev in devices:
        try:
            dev_info = _format_device_info(dev)
        except Exception:
            continue

        # Check if already connected
        already_connected = any(
            dev_data["info"]["serial_number"] == dev_info["serial_number"]
            for dev_data in device_pool.values()
        )

        if already_connected:
            continue

        # Apply filters
        if params.serial_number is not None:
            if dev_info["serial_number"] == params.serial_number:
                matched.append((dev, dev_info))
            continue

        if params.manufacturer is not None and dev_info["manufacturer"] != params.manufacturer:
            continue
        if params.product is not None and dev_info["product"] != params.product:
            continue

        matched.append((dev, dev_info))

    if len(matched) == 0:
        error_msg = (
            "No USBTMC device matched the criteria, or all matching devices are already connected. "
            "Use usbtmc_list_devices() to see available devices."
        )
        logger.error(error_msg)
        return {"error": error_msg}

    if len(matched) > 1:
        device_list = ", ".join(f"{d[1]['product']} ({d[1]['serial_number']})" for d in matched)
        error_msg = (
            f"Multiple devices matched ({len(matched)}): {device_list}. "
            "Please specify serial_number to uniquely identify the target device."
        )
        logger.error(error_msg)
        return {"error": error_msg}

    device, dev_info = matched[0]

    try:
        # Create new USBTMC instance
        usbtmc = USBTMC()
        usbtmc.open(device)

        device_pool[device_id] = {"instance": usbtmc, "info": dev_info}

        logger.info(f"Connected to {dev_info['product']} as device_id={device_id}")

        return {
            "device_id": device_id,
            "manufacturer": dev_info["manufacturer"],
            "product": dev_info["product"],
            "serial_number": dev_info["serial_number"],
            "message": f"Successfully connected to {dev_info['product']}",
        }

    except Exception as e:
        error_msg = f"Connection failed: {e}"
        logger.error(error_msg)
        return {"error": error_msg}


@mcp.tool(
    name="usbtmc_disconnect",
    annotations={
        "title": "Disconnect from USBTMC Device",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def usbtmc_disconnect(params: DeviceIdInput) -> dict[str, Any]:
    """
    Disconnect from a specific USBTMC device.

    Args:
        params (DeviceIdInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()

    Returns:
        dict: Result containing:
            - success (bool): Whether the disconnection was successful
            - device_id (int): The disconnected device_id
            - product (str): Product name of the disconnected device
            - message (str): Success or error message
    """
    global device_pool

    try:
        usbtmc, info = _get_device(params.device_id)
    except ValueError as e:
        return {"success": False, "device_id": params.device_id, "message": str(e)}

    product_name = info["product"]

    try:
        usbtmc.close()
        del device_pool[params.device_id]

        logger.info(f"Disconnected from {product_name} (device_id={params.device_id})")
        return {
            "success": True,
            "device_id": params.device_id,
            "product": product_name,
            "message": f"Disconnected from {product_name}",
        }

    except Exception as e:
        error_msg = f"Disconnect failed: {e}"
        logger.error(error_msg)
        return {
            "success": False,
            "device_id": params.device_id,
            "product": product_name,
            "message": error_msg,
        }


# ============================================================
# MCP Tools - Communication
# ============================================================
@mcp.tool(
    name="usbtmc_clear",
    annotations={
        "title": "Clear Device Buffers",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def usbtmc_clear(params: DeviceIdInput) -> dict[str, Any]:
    """
    Clear the device input/output buffers and reset communication state.

    Use this to recover from communication errors or stuck states.

    Args:
        params (DeviceIdInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()

    Returns:
        dict: Result containing:
            - success (bool): Whether the clear operation was successful
            - device_id (int): The target device_id
            - message (str): Success or error message
    """
    try:
        usbtmc, info = _get_device(params.device_id)
    except ValueError as e:
        return {"success": False, "device_id": params.device_id, "message": str(e)}

    try:
        usbtmc.clear()
        logger.info(f"[device_id={params.device_id}] Clear")
        return {
            "success": True,
            "device_id": params.device_id,
            "message": f"Cleared buffers for device {params.device_id} ({info['product']})",
        }

    except Exception as e:
        error_msg = f"Clear failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        return {"success": False, "device_id": params.device_id, "message": error_msg}


@mcp.tool(
    name="usbtmc_send",
    annotations={
        "title": "Send Command to Device",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def usbtmc_send(params: SendInput) -> dict[str, Any]:
    """
    Send a SCPI command to a specific USBTMC device.

    Use for SET commands (without '?') that configure device settings.
    For QUERY commands (with '?'), use usbtmc_query() instead.

    This function only sends data; use usbtmc_receive() to read responses,
    or use usbtmc_query() for send+receive in one operation.

    Args:
        params (SendInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()
            - command (str): SCPI command string to send

    Returns:
        dict: Result containing:
            - success (bool): Whether the send was successful
            - device_id (int): The target device_id
            - command (str): The command that was sent
            - message (str): Success or error message
    """
    try:
        usbtmc, _ = _get_device(params.device_id)
    except ValueError as e:
        return {
            "success": False,
            "device_id": params.device_id,
            "command": params.command,
            "message": str(e),
        }

    try:
        usbtmc.write(params.command)
        logger.info(f"[device_id={params.device_id}] Sent: {params.command}")
        return {
            "success": True,
            "device_id": params.device_id,
            "command": params.command,
            "message": "Command sent successfully",
        }

    except Exception as e:
        error_msg = f"Send failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        return {
            "success": False,
            "device_id": params.device_id,
            "command": params.command,
            "message": error_msg,
        }


@mcp.tool(
    name="usbtmc_receive",
    annotations={
        "title": "Receive Data from Device",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def usbtmc_receive(params: ReceiveInput) -> dict[str, Any]:
    """
    Receive data from a specific USBTMC device.

    Args:
        params (ReceiveInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()
            - max_bytes (int): Maximum bytes to read (default: 1024)

    Returns:
        dict: Result containing:
            - success (bool): Whether data was received
            - device_id (int): The target device_id
            - data (str or None): Received data string, or None if no data/timeout
            - message (str): Success or error message
    """
    try:
        usbtmc, _ = _get_device(params.device_id)
    except ValueError as e:
        return {"success": False, "device_id": params.device_id, "data": None, "message": str(e)}

    try:
        data = usbtmc.read(params.max_bytes)
        if data:
            logger.info(f"[device_id={params.device_id}] Received: {data}")
            return {
                "success": True,
                "device_id": params.device_id,
                "data": data,
                "message": "Data received successfully",
            }
        else:
            return {
                "success": False,
                "device_id": params.device_id,
                "data": None,
                "message": "No data received (timeout)",
            }

    except Exception as e:
        error_msg = f"Receive failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        return {"success": False, "device_id": params.device_id, "data": None, "message": error_msg}


@mcp.tool(
    name="usbtmc_query",
    annotations={
        "title": "Query Device (Send + Receive)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def usbtmc_query(params: QueryInput) -> dict[str, Any]:
    """
    Send a SCPI command and immediately receive the response.

    Use for QUERY commands (with '?') that request information from the device.
    For SET commands (without '?'), use usbtmc_send() instead.

    This is a convenience function combining send and receive operations.

    Args:
        params (QueryInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()
            - command (str): SCPI command string to send (usually ends with '?')
            - wait_time (float): Time to wait between send and receive (default: 0.1s)
            - max_bytes (int): Maximum bytes to read (default: 1024)

    Returns:
        dict: Result containing:
            - success (bool): Whether the query was successful
            - device_id (int): The target device_id
            - command (str): The command that was sent
            - response (str or None): Response data, or None if no data/timeout
            - message (str): Success or error message

    Note:
        Wait time recommendations:
        - Simple queries (*IDN?, status): 0.1s (default)
        - Querying existing measurements: 0.1s
        - After changing settings: 0.5-2.0s
    """
    try:
        usbtmc, _ = _get_device(params.device_id)
    except ValueError as e:
        return {
            "success": False,
            "device_id": params.device_id,
            "command": params.command,
            "response": None,
            "message": str(e),
        }

    try:
        usbtmc.write(params.command)
        logger.info(f"[device_id={params.device_id}] Query sent: {params.command}")

        time.sleep(params.wait_time)

        response = usbtmc.read(params.max_bytes)
        if response:
            logger.info(f"[device_id={params.device_id}] Query received: {response}")
            return {
                "success": True,
                "device_id": params.device_id,
                "command": params.command,
                "response": response,
                "message": "Query completed successfully",
            }
        else:
            return {
                "success": False,
                "device_id": params.device_id,
                "command": params.command,
                "response": None,
                "message": "No response received (timeout). Try increasing wait_time.",
            }

    except Exception as e:
        error_msg = f"Query failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        return {
            "success": False,
            "device_id": params.device_id,
            "command": params.command,
            "response": None,
            "message": error_msg,
        }


# ============================================================
# MCP Tools - Vendor-Specific Operations
# ============================================================
@mcp.tool(
    name="usbtmc_screenshot_keysight_display",
    annotations={
        "title": "Capture and Display Screenshot from Keysight/Agilent Device",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def usbtmc_screenshot_keysight_display(params: DeviceIdInput) -> Image:
    """
    Capture screenshot from a Keysight/Agilent oscilloscope.

    Args:
        params (DeviceIdInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()

    Returns:
        Image
    """
    try:
        usbtmc, info = _get_device(params.device_id)
    except ValueError as e:
        raise ValueError(f"Device connection failed: {e}")

    try:
        usbtmc.write(":DISPlay:DATA? PNG")
        time.sleep(0.2)

        binary_data = usbtmc.read_block_data()

        logger.info(
            f"[device_id={params.device_id}] Returning screenshot as Image object "
            f"({len(binary_data)} bytes)"
        )

        return Image(data=binary_data, format="png")

    except Exception as e:
        error_msg = f"Screenshot failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        raise RuntimeError(error_msg)


@mcp.tool(
    name="usbtmc_screenshot_tektronix_display",
    annotations={
        "title": "Capture and Display Screenshot from Tektronix Device",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def usbtmc_screenshot_tektronix_display(params: DeviceIdInput) -> Image:
    """
    Capture screenshot from a Tektronix oscilloscope.

    Args:
        params (DeviceIdInput): Input containing:
            - device_id (int): The unique identifier returned by usbtmc_connect()

    Returns:
        Image
    """
    try:
        usbtmc, info = _get_device(params.device_id)
    except ValueError as e:
        raise ValueError(f"Device connection failed: {e}")

    try:
        usbtmc.write("HARDCopy START")
        time.sleep(0.2)

        binary_data = usbtmc.read_bytes(1024 * 1024)

        logger.info(
            f"[device_id={params.device_id}] Returning screenshot as Image object "
            f"({len(binary_data)} bytes)"
        )

        return Image(data=binary_data, format="png")
        
    except Exception as e:
        error_msg = f"Screenshot failed: {e}"
        logger.error(f"[device_id={params.device_id}] {error_msg}")
        raise RuntimeError(error_msg)


# ============================================================
# Entry Point
# ============================================================
def main():
    mcp.run()


if __name__ == "__main__":
    main()