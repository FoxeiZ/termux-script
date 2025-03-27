import sys

if __name__ == "__main__":
    if sys.argv[1] == "client":
        from lib.client import AudioClient

        client = AudioClient()
        client.connect()
        client.start()

    else:
        from codec.opus import OpusCodec
        from lib.server import AudioServer

        server = AudioServer(host="0.0.0.0", buffer_size=1920, codec_cls=OpusCodec)
        try:
            server.start()
        except KeyboardInterrupt:
            server.close()
