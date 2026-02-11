# Prism Desktop
**A Home Assistant PC App for Windows & Linux**

Prism Desktop brings Home Assistant to your PC with a modern, lightweight desktop experience.  
It features a sleek dashboard with smooth animations, build in notifications, intuitive drag-and-drop customization, and deep integration with Home Assistant entities.


<img
  width="418"
  height="497"
  alt="Prism Desktop – Home Assistant PC app dashboard"
  src="https://github.com/user-attachments/assets/14e5d292-98be-4270-9ce3-9f9661499370"
/>



## Features

- **System Tray Integration**: The app stays tucked away in your tray until you need it.
- **PC notifications**: Send notifications to your PC via persistent_notification.create
- **Morphing Controls**: Click and hold widgets to expand them into granular controls like dimmers or thermostats.
- **Drag & Drop Customization**: Rearrange your dashboard grid simply by dragging icons around.
- **Real-time Sync**: Uses Home Assistant's WebSocket API for instant state updates.
- **Customizable Appearance**: Choose from different border effects (like Rainbow or Aurora) and customize button colors.
- **Keyboard Shortcuts**: Global hotkeys for toggling the app and controlling individual buttons.

## Supported Entity Types
- Camera
- Climate
- Curtain / Cover
- Light / Switch
- Scene
- Script
- Sensor
- Weather

## Keyboard Shortcuts
- **Open / Close App**: Use the shortcut defined in Settings under 'App toggle'.
- **Toggle Buttons**: Use `Alt + 1-9` (or your preferred modifier key set in Settings).
- **Custom Shortcuts**: Define custom shortcuts for any button via the Add/Edit menu.

## How to Use (Lights)
- **Click**: Toggle the light on/off.
- **Hold (Long Press)**: Open the dimmer overlay for brightness control.

## Installation

### Windows Installer
Download the latest `PrismDesktopSetup.exe` from the Releases page. This will install the app and optionally set it to start with Windows.

### Linux Installer
Download the latest `appimage` from the Releases page. or download and run from source.    
GNOME: make sure to install `AppIndicator and KStatusNotifierItem Support` through `Extension Manager` first. 

```keyboard shortcuts doesn't work on wayland yet.```

### Manual / Portable
You can also download the standalone `.exe` if you prefer not to install anything. Just run it, and it will create a configuration file in the same directory.

## Running from Source

If you want to modify the code or run it manually:

1. Clone this repository.
   ```bash
   pip install -r requirements.txt
   ```
   Or manually:
   ```bash
   pip install PyQt6 pystray aiohttp Pillow requests pynput winotify keyring
   ```
3. Run the application:
   ```bash
   python main.py
   ```

## Configuration

Upon first launch, you will be asked for your Home Assistant URL and a Long-Lived Access Token. You can generate this token in your Home Assistant profile settings.

<img width="420" height="729" alt="image" src="https://github.com/user-attachments/assets/b77c9c29-ea0e-4f42-be96-fafc37e41b8c" />



## Building

### Windows
To build the executable yourself, run the included build script:

```bash
python build_exe.py
```

This will run PyInstaller and generate a single-file executable in the `dist` folder.

To build the installer, open `setup.iss` with [Inno Setup](https://jrsoftware.org/isdl.php) and compile it.

### Linux (AppImage)
1. Download `appimagetool-x86_64.AppImage` from the [appimagetool releases](https://github.com/AppImage/appimagetool/releases) and place it in the project folder.
2. Run the build script:

```bash
python3 build_appimage.py
```

This will build the binary, create an AppDir, and package it into an AppImage.
