# Prism Desktop

A modern, native Windows client for Home Assistant that lives in your system tray.

I built this because I wanted a faster, more elegant way to control my smart home without opening a browser tab. It features a sleek dashboard with fluid animations, drag-and-drop customization, and deep integration with Home Assistant entities.

<img width="439" height="399" alt="prismdesktop" src="https://github.com/user-attachments/assets/bfb576a4-d65f-4f3f-a9da-e5e26f89e404" />


## Features

- **System Tray Integration**: The app stays tucked away in your tray until you need it.
- **Morphing Controls**: Click and hold widgets to expand them into granular controls like dimmers or thermostats.
- **Drag & Drop Customization**: Rearrange your dashboard grid simply by dragging icons around.
- **Real-time Sync**: Uses Home Assistant's WebSocket API for instant state updates.
- **Customizable Appearance**: Choose from different border effects (like Rainbow or Aurora) and customize button colors.

## Supported entity types:
- light / switch
- sensor
- climate
- curtain / cover
- script
- scene
- image
- camera

## Keyboard shortcuts
- open / close app with shortcut defined in settings under 'App toggle'
- buttons can be toggled with eg. ALT + 1,2,3,4 etc - you can choose your prefered modifier in settings.
- if you want custom keyboard shortcuts for any button, it can be toggled and configurated in add/edit button menu.

## How to use (light)
- click on button to toggle
- hold button to open dimmer

## Installation

### Windows Installer
Download the latest `PrismDesktopSetup.exe` from the Releases page. This will install the app and optionally set it to start with Windows.

### Manual / Portable
You can also download the standalone `.exe` if you prefer not to install anything. Just run it, and it will create a configuration file in the same directory.

## Running from Source

If you want to modify the code or run it manually:

1. Clone this repository.
2. Install the dependencies:
   ```
   pip install PyQt6 requests websocket-client
   ```
3. Run the application:
   ```
   python main.py
   ```

## Configuration

Upon first launch, you will be asked for your Home Assistant URL and a Long-Lived Access Token. You can generate this token in your Home Assistant profile settings.

<img width="429" height="691" alt="image" src="https://github.com/user-attachments/assets/a8a2a005-cda2-4068-80e8-25643924a8ed" />


## Building

To build the executable yourself, run the included build script:

```
python build_exe.py
```

This will run PyInstaller and generate a single-file executable in the `dist` folder.
