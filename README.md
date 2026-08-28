# USBTMC MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for controlling multiple USBTMC (USB Test and Measurement Class) devices simultaneously using SCPI commands.

## Features

- **Multi-Device Support**: Connect and control up to 16 instruments at once.
- **Unified Interface**: Use standard SCPI commands via a simplified MCP toolset.
- **Keysight Support**: Includes specialized tools to unlock Keysight modular instruments from firmware mode to USBTMC mode.
- **Screenshot Capture**: Take screenshots from Keysight/Agilent and Tektronix oscilloscopes.
- **State Management**: Automatic tracking of device IDs and connection states.

## Requirements

- Python >= 3.13
- `mcp[cli]` **2.x** (v0.2.0 and later; use `usbtmc-lite-mcp` 0.1.x if you are pinned to `mcp` 1.x)

### Windows Additional Setup
This project uses libUSB as a backend for USB communication. On Windows, you need to install a compatible USB driver for your device using [Zadig](https://zadig.akeo.ie/).

1. Download and run Zadig
2. Select your target USB device from the dropdown (enable **Options → List All Devices** if it does not appear)
3. Select **WinUSB** as the driver and click **Replace Driver**

> **Note:** Replacing the driver will remove the device's original functionality (e.g., HID recognition). To restore it, uninstall the driver from Device Manager and reconnect the device.

## Installation

Using `uv` (recommended):

```bash
uv pip install usbtmc-lite-mcp
```

Or via `pip`:

```bash
pip install usbtmc-lite-mcp
```

Or from source:

```bash
git clone https://github.com/NaoNaoMe/usbtmc-lite-mcp.git
cd usbtmc-lite-mcp
uv sync
```

## Configuration

### Claude Desktop Config

Add the server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "usbtmc-lite-mcp": {
      "command": "uvx",
      "args": [
        "usbtmc-lite-mcp"
      ]
    }
  }
}
```

## Tools Overview

### Connection

- `usbtmc_list_devices`: Scan the USB bus for available USBTMC instruments.
- `usbtmc_list_connected_devices`: List all currently active device connections.
- `usbtmc_connect`: Establish a connection to a specific device (returns a `device_id`).
- `usbtmc_disconnect`: Close the connection to a device.

### Communication

- `usbtmc_query`: Send a SCPI query (e.g., `*IDN?`) and receive the response.
- `usbtmc_send`: Send a SCPI command.
- `usbtmc_receive`: Manually reads the response.
- `usbtmc_clear`: Clear device buffers and reset communication state.

### Keysight / Tektronix Utilities

- `usbtmc_unlock_keysight_devices`: Switch Keysight modular instruments to USBTMC mode.
- `usbtmc_screenshot_keysight_display`: Capture a screenshot from a Keysight/Agilent oscilloscope.
- `usbtmc_screenshot_tektronix_display`: Capture a screenshot from a Tektronix oscilloscope.

## Example Workflow

1.  **List Devices**: Call `usbtmc_list_devices` to find your instrument's serial number.
2.  **Connect**: Call `usbtmc_connect` with the `serial_number`. It will return a `device_id` (e.g., `0`).
3.  **Identify**: Call `usbtmc_query` with `device_id: 0` and `command: "*IDN?"`.
4.  **Configure**: Call `usbtmc_send` to set parameters.
5.  **Disconnect**: Call `usbtmc_disconnect` when finished.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
