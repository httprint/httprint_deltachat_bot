# HTTPrint Delta Chat Bot

Delta Chat bot for uploading files to an HTTPrint server. By default, Delta Chat
selects an available Chatmail relay; no email address or password is needed.
Chatmail uses email protocols internally.

The bot stores its Delta Chat identity, encryption keys, sessions, and
attachments in `app/data` (or `DELTACHAT_ACCOUNTS_DIR`). On first startup it
creates `contact-qr.svg` and `contact-qr.png` in that directory. Share either
QR with users to add the bot. If a QR file is missing, or the configured
identity or transports change, it is regenerated.
Delta Chat accounts are always stored in the `accounts` subdirectory. The QR
files, relay state and optional `icon.png` stay directly in the data directory.
Keep this directory private and backed up: losing it means losing the bot's
identity. In Docker, this directory is a persistent volume.

## How to

### Docker

Add the following service to your `docker-compose.yml` file:

```yaml
  httprint_delta_bot:
    build: https://github.com/httprint/httprint_telegram_bot.git
    container_name: httprint_delta_bot  # optional
    environment:
      - HTTPRINT_HOST=http://httprint.example.com:7777 # optional; defaults to http://httprint:7777
      # - CHATMAIL_RELAYS=chatmail.bytea.org,nine.testrun.org # optional comma-separated relay hostnames
      - BOT_NAME=HTTPrint # optional display name
      # - BOT_ICON=/app/bot-icon.png # optional profile image; mount the file in the container
      - LOG_LEVEL=DEBUG  # optional; defaults to INFO
      - DELTACHAT_ACCOUNTS_DIR=/app/data # optional; defaults beside app.py
    volumes:
      - httprint_delta_data:/app/data
    restart: unless-stopped

volumes:
  httprint_delta_data:
  ```
  
  * `CHATMAIL_RELAYS` selects one or more relay hostnames, separated by commas. If omitted or empty, Delta Chat automatically selects an available relay for a new account and retains existing transports for an existing account. Delta Chat may add further transports automatically in the background. With an explicit list, existing transports on selected relays are reused; new ones are registered before transports on unselected relays are removed. Removing a relay can affect previously shared invitations; distribute the regenerated QR after changing the list.
  * `BOT_NAME` sets the profile display name; it defaults to `HTTPrint`. `BOT_ICON` optionally points to a local profile image. Mount that file when using Docker. If `BOT_ICON` is omitted or empty, the bot uses `icon.png` in the account data directory when present; otherwise it preserves the existing profile image. The image is applied at startup, so restart the bot after changing it. The QR retains the Delta Chat logo.
  * The first startup requires internet access to the selected Chatmail relays. If registration is interrupted, missing transports are added on the next start.
  * `HTTPRINT_HOST` is your httprint server's URI/URL. If run in the same docker-compose stack, name your httprint service `httprint` and omit this env var.
  * The bot marks incoming messages as read and asks Delta Chat to delete them locally and from the mail server before sending opened attachments to HTTPrint. Bot replies are deleted after Delta Chat confirms they were sent; pending or failed replies are kept. On startup, previously sent replies are cleaned up as well. If deletion fails, the bot reports the error and stops rather than continuing the upload.
  * Delta Chat stores incoming attachments temporarily on disk. Its native housekeeping removes unreferenced files, preserving shared files such as profile images; it does not guarantee immediate removal. The relay, HTTPrint, backups, SQLite storage and logs may retain data independently. This is not a guarantee of forensic erasure.

Send a file to the Delta Chat account.
The bot sends the original filename reported by Delta Chat to HTTPrint, when
available; otherwise it uses the local blob filename.

Add print options to the message text:

* `single` or `one`: one-sided
* `long`, `duplex`, or `two`: two-sided, long edge
* `short`: two-sided, short edge
* `a3`, `a4`, or `a5`: paper size
* `mono` or `bw`: monochrome
* `color`: color
* A number: number of copies
  

# License and copyright

Copyright 2023 itec <itec@ventuordici.org>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Printer icon created by Good Ware - Flaticon
