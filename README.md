# phone-photo-tools

Two small Python scripts that get photos from your phone onto your laptop over the local Wi-Fi.
No app, no cable, no cloud, no messenger detour: the laptop shows a QR code, the phone opens a
web page and uploads the pictures.

Both scripts use only the Python standard library. The QR code is drawn with
[`qrencode`](https://fukuchi.org/works/qrencode/) (`apt install qrencode`, `pacman -S qrencode`, `brew install qrencode`).
Without it, the scripts still print the address to type in.

## fotodoku: photo documentation with titles and notes

Made for commissioning jobs, repairs or build logs, where dozens of photos pile up and nobody
remembers afterwards which one shows what.

```sh
./fotodoku.py ~/projects/commissioning --port 8000
```

- The laptop opens an overview with the QR code, a folder picker and all photos taken so far.
- On the phone you enter a title (for example *Control cabinet*) and an optional note, then take
  photos or pick them from the gallery.
- Photos are saved as `Control_cabinet_001.jpg`, `Control_cabinet_002.jpg`, … numbered per title.
- A `comments.md` in the folder lists every title, number, note and image, ready to drop into a report:

  ```markdown
  # Control cabinet

  ## 001
  Door open

  ![Control_cabinet_001.jpg](Control_cabinet_001.jpg)
  ```

- An existing folder can be continued: `comments.md` and the existing photos are read in and the
  numbering carries on.

Options: `--ip` sets the address shown in the QR code (if the detected one is wrong),
`--no-browser` skips opening the overview, `--titles "Front,Back,Label"` offers one-tap title
buttons on the phone and `--title "Front"` pre-fills the title. Without a preset, the phone
remembers the last title per folder, so a new folder does not start with an old title.

## fotodrop: just get the photos over

The quick version without titles and notes. Start it in the folder where the pictures should go:

```sh
cd ~/Desktop && ./fotodrop.py
```

Scan the QR code in the terminal, take photos, and they land in the current folder as
`foto_20261109_143012.jpg`. If a transfer fails, the phone keeps the photo and offers to send it
again. Ctrl+C quits and shows how many photos were saved.

## Network and security

- Laptop and phone must be on the same network. Open the port in your firewall, for example
  `sudo ufw allow 8000/tcp`, and close it again afterwards if you like.
- Both tools put a random token into the phone address (`http://192.168.1.20:8000/<token>/…`).
  Other devices on the network get a 404 without it. The token changes every time you start the tool.
- The connection is plain HTTP inside your local network. Do not expose the port to the internet.
- fotodoku's folder picker only works on the laptop itself; the phone can only upload into the chosen folder.

## Install

Copy or link the scripts somewhere in your `PATH`, for example:

```sh
ln -s "$PWD/fotodoku.py" ~/.local/bin/fotodoku
ln -s "$PWD/fotodrop.py" ~/.local/bin/fotodrop
```

## License

MIT, see [LICENSE](LICENSE).
