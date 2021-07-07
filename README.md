# 🔊 TgRadio

Radio server for Telegram groups.

## Requirements

 - [Python 3.8+](https://www.python.org/)
 - [ffmpeg](https://www.ffmpeg.org/)

## How to use

1. Create `.env` in root:
    ```
    SESSION_NAME=<Name of .session file | Default: TgRadio>
    TG_API_ID=<token_id>
    TG_API_HASH=<token_hash>
    TG_PHONE_NUMBER=<you phone number>
    AVAILABLE_GROUPS=<Group IDs separated by commas | Default: without available filter>
    CMD_PREFIX=<Prefix for commands | Default: !>
    ```
   *For get `id` and `hash` see [this](https://core.telegram.org/api/obtaining_api_id)*

2. Create `default.raw` audio for loop playing if channel not have tracks. use [ffmpeg](https://www.ffmpeg.org/) to convert and move output file to root directory.
    ```shell
    $ ffmpeg -i path/to.mp3 -f s16le -ac 2 -ar 48000 -acodec pcm_s16le default.raw
    ```

3. Run server
    ```shell
    $ pip install -r requirements.txt
    $ python TgRadio.py
    ```

4. Add you **radio** account in group.

5. Send `!help` in group for get help.

---

- **License:** © 2021 M.Price.<br>See the [LICENSE file](LICENSE) for license rights and limitations (MIT).
