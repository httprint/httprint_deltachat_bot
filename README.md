# HTTPrint Delta Chat Bot

Delta Chat bot for uploading files to an HTTPrint server. By default, Delta Chat
selects an available Chatmail relay; no email address or password is needed.
Chatmail uses email protocols internally.

The bot stores its Delta Chat identity, encryption keys and sessions in
`app/data` (or `DELTACHAT_ACCOUNTS_DIR`). On first startup it
creates `contact.svg`, `contact.png`, and `contact.txt` in that
directory. Share a QR or the invitation link in the text file with users to
add the bot.
The QR files, relay state and optional `icon.png` stay directly in the data directory.
Keep this directory private and backed up: losing it means losing the bot's
identity. In Docker, this directory is a persistent volume.

## How to

### Docker

Add the following service to your `docker-compose.yml` file:

```yaml
  httprint_delta_bot:
    build: https://github.com/httprint/httprint_deltachat_bot.git
    container_name: httprint_delta_bot  # optional
    environment:
      - HTTPRINT_HOST=http://httprint.example.com:7777 # optional; defaults to http://httprint:7777
      # - CHATMAIL_RELAYS=chatmail.bytea.org,nine.testrun.org # optional comma-separated relay hostnames
      - BOT_NAME=HTTPrint # optional display name
      # - BOT_ICON=bot-icon.png # optional profile image inside /app/data
      - LOG_LEVEL=DEBUG  # optional; defaults to INFO
      - DELTACHAT_ACCOUNTS_DIR=/app/data # optional; defaults beside app.py
    volumes:
      - httprint_delta_data:/app/data
    restart: unless-stopped

volumes:
  httprint_delta_data:
  ```
  
  * `CHATMAIL_RELAYS` selects one or more relay hostnames, separated by commas. If omitted or empty, Delta Chat automatically selects an available relay for a new account and retains existing transports for an existing account. Delta Chat may add further transports automatically in the background. With an explicit list, existing transports on selected relays are reused; new ones are registered before transports on unselected relays are removed. Removing a relay can affect previously shared invitations; distribute the regenerated QR after changing the list.
  * `BOT_NAME` sets the profile display name; it defaults to `HTTPrint`. `BOT_ICON` optionally names a profile image inside the data directory. Relative paths are resolved from that directory; absolute paths must also remain inside it, including after resolving symbolic links. Place the image in the mounted data volume when using Docker. If `BOT_ICON` is omitted or empty, the bot uses `icon.png` in the data directory when present; otherwise it preserves the existing profile image. The image is applied at startup, so restart the bot after changing it. The QR retains the Delta Chat logo.
  * The first startup requires internet access to the selected Chatmail relays. If registration is interrupted, missing transports are added on the next start.
  * `HTTPRINT_HOST` is your httprint server's URI/URL. If run in the same docker-compose stack, name your httprint service `httprint` and omit this env var.

Send a file to the Delta Chat account.
The bot sends the filename reported by Delta Chat to HTTPrint.

Add print options to the message text:

* `single` or `one`: one-sided
* `long`, `duplex`, or `two`: two-sided, long edge
* `short`: two-sided, short edge
* `a3`, `a4`, or `a5`: paper size
* `mono` or `bw`: monochrome
* `color`: color
* A number: number of copies
  

# License and copyright

Copyright 2026 itec <itec@ventuordici.org>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Printer icon created by Good Ware - Flaticon
