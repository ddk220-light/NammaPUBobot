"""Play bundled 20 ms Opus packets without FFmpeg or a live encoder."""
from nextcord import AudioSource
from nextcord.oggparse import OggStream


class OpusClip(AudioSource):
	def __init__(self, path):
		# At most 32 KB per bundled clip. Close the file before the audio thread
		# starts so timeout cleanup cannot race an in-flight file read.
		with open(path, 'rb') as stream:
			packets = list(OggStream(stream).iter_packets())
		if len(packets) < 3 or not packets[0].startswith(b'OpusHead') or not packets[1].startswith(b'OpusTags'):
			raise ValueError('Expected a prepared Opus clip')
		self.packets = iter(packets[2:])

	def read(self):
		return next(self.packets, b'')

	def is_opus(self):
		return True

	def cleanup(self):
		self.packets = iter(())
