{
  description = "Prism Desktop";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems
          (system: f (import nixpkgs { inherit system; }));
    in
    {
      packages = forAllSystems (pkgs:
        let
          version = "1.4.2";

          pythonEnv = pkgs.python3.withPackages (ps: with ps; [
            pyqt6
            pystray
            aiohttp
            pillow
            pynput
            requests
            keyring
            cryptography
            secretstorage
            dbus-next
            qasync
          ]);
        in
        {
          default = pkgs.stdenvNoCC.mkDerivation rec {
            pname = "prism-desktop";
            inherit version;

            src = ./.;

            nativeBuildInputs = [
              pkgs.python3
              pkgs.makeWrapper
              pkgs.copyDesktopItems
              pkgs.qt6.wrapQtAppsHook
            ];

            buildInputs = [
              pkgs.qt6.qtbase
              pkgs.qt6.qtwayland
            ];

            dontBuild = true;

            postPatch = ''
              ${pkgs.python3}/bin/python - <<'PY'
              from pathlib import Path

              # Fix config path so Prism does not try to write into /nix/store
              p = Path("core/utils.py")
              s = p.read_text()

              old = "        return Path(__file__).parent.parent / filename"
              new = (
                  "        app_data = get_platform_config_dir()\n"
                  "        app_data.mkdir(parents=True, exist_ok=True)\n"
                  "        return app_data / filename"
              )

              if old not in s:
                  raise SystemExit("expected return statement not found in core/utils.py")

              p.write_text(s.replace(old, new, 1))

              # Disable startup self-update check for packaged builds
              p = Path("main.py")
              s = p.read_text()

              old = "QTimer.singleShot(2000, self.check_for_updates)"
              if old in s:
                  s = s.replace(old, "# updater disabled in Nix package", 1)

              p.write_text(s)
              PY
            '';

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/prism-desktop
              cp -r core services ui $out/share/prism-desktop/
              cp main.py icon.png materialdesignicons-webfont.ttf mdi_mapping.json \
                LICENSE README.md \
                $out/share/prism-desktop/

              mkdir -p $out/bin
              makeWrapper ${pythonEnv}/bin/python $out/bin/prism-desktop \
                --add-flags "$out/share/prism-desktop/main.py"

              mkdir -p $out/share/icons/hicolor/256x256/apps
              cp icon.png $out/share/icons/hicolor/256x256/apps/prism-desktop.png

              runHook postInstall
            '';

            preFixup = ''
              wrapQtApp "$out/bin/prism-desktop"
            '';

            desktopItems = [
              (pkgs.makeDesktopItem {
                name = "prism-desktop";
                exec = "prism-desktop";
                icon = "prism-desktop";
                desktopName = "Prism Desktop";
                genericName = "Home Assistant desktop dashboard";
                comment = "A customizable desktop dashboard for Home Assistant";
                categories = [ "Utility" ];
                startupNotify = true;
              })
            ];

            meta = with pkgs.lib; {
              description = "Home Assistant desktop dashboard";
              homepage = "https://github.com/lasselian/prism-desktop";
              license = licenses.mit;
              platforms = platforms.linux;
              mainProgram = "prism-desktop";
            };
          };
        });

      apps = forAllSystems (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/prism-desktop";
        };
      });
    };
}

