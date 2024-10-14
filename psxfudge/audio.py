# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import math
from struct  import Struct
from enum    import IntFlag
from pathlib import Path

import numpy, av
from .native import SPUBlockEncoder
from .util   import alignMutableToMultiple, swapEndianness

## Sound wrapper class

VAG_HEADER_STRUCT  = Struct("> 4s 4I 10x B x 16s")
VAG_HEADER_MAGIC   = b"VAGp"
VAGI_HEADER_MAGIC  = b"VAGi"
VAG_HEADER_VERSION = 0x20

class LoopFlags(IntFlag):
	LOOP           = 1
	SUSTAIN        = 2
	SET_LOOP_POINT = 4

def getVAGHeader(length, channels, sampleRate, interleave = 0, name = ""):
	return VAG_HEADER_STRUCT.pack(
		VAGI_HEADER_MAGIC if interleave else VAG_HEADER_MAGIC,
		VAG_HEADER_VERSION,
		swapEndianness(interleave, 32),
		length,
		sampleRate,
		channels,
		name.encode("ascii")
	)

class SoundWrapper:
	"""
	Wrapper class for converted SPU ADPCM audio samples.
	"""

	def __init__(self, data, sampleRate, name = ""):
		if data.ndim != 2:
			raise ValueError("ADPCM data must be 2-dimensional")

		self.data       = data
		self.sampleRate = sampleRate
		self.name       = name

	def getSPUSampleRate(self):
		return round(self.sampleRate * 0x1000 / 44100)

	def toVAG(self, align = None):
		vag = bytearray(getVAGHeader(
			self.data.shape[1],
			self.data.shape[0],
			self.sampleRate,
			self.data.shape[1] if self.data.shape[0] > 1 else 0,
			self.name
		))
		if align:
			alignMutableToMultiple(vag, align)

		for channel in self.data:
			vag.extend(channel)
			if align:
				alignMutableToMultiple(vag, align)

		return vag

## Audio file importer

def _importAudio(avFile, channels, sampleRate, chunkLength = 0):
	resampler = av.AudioResampler("s16p", "stereo" if channels == 2 else "mono", sampleRate)
	fifo      = av.AudioFifo()

	with avFile:
		for inputFrame in avFile.decode(audio = 0):
			# FIXME: apparently there is a PyAV bug with the FIFO and resampler
			# implementation that messes up handling of non-sequential frames.
			# Brutally deleting the presentation timestamp seems to "fix" the
			# issue.
			inputFrame.pts = None

			for frame in resampler.resample(inputFrame):
				fifo.write(frame)

			if chunkLength:
				while frame := fifo.read(chunkLength):
					yield frame

	# Flush any samples buffered by the resampler, then flush the FIFO.
	for frame in resampler.resample(None):
		fifo.write(frame)

	while frame := fifo.read(chunkLength, True):
		yield frame

## SPU ADPCM conversion and packing

def convertAudioStream(avFile, options):
	"""
	Decodes a PyAV file object, resamples it and re-encodes it into a series of
	SPU ADPCM chunks using the given dict of options. Yields NumPy byte arrays
	containing encoded chunks for each channel.
	"""

	channels    = int(options["channels"])
	sampleRate  = int(options["sampleRate"])
	interleave  = int(options["interleave"])
	loopOffset  = float(options["loopOffset"])

	_loopOffset = round(loopOffset * sampleRate)
	encoders    = [ SPUBlockEncoder(_loopOffset) for _ in range(channels) ]

	for frame in _importAudio(
		avFile,
		channels,
		sampleRate,
		interleave // 16 * 28 # Chunk length in samples
	):
		pcmData = frame.to_ndarray()

		if align := (pcmData.shape[1] % 28):
			pcmData = numpy.c_[
				pcmData,
				numpy.zeros(( channels, 28 - align ), numpy.int16)
			]

		# Set the loop and sustain flags to ensure the SPU jumps to the next
		# chunk after playing this one.
		length = pcmData.shape[1] // 28 * 16
		data   = numpy.zeros(( channels, length ), numpy.uint8)

		for encoder, samples, output in zip(encoders, pcmData, data):
			encoder.encode(samples, output, LoopFlags.LOOP | LoopFlags.SUSTAIN)

		yield data

def convertSound(avFile, options):
	"""
	Similar to convertAudioStream() but buffers the entire converted file in
	memory. Returns a SoundWrapper object.
	"""

	channels   = int(options["channels"])
	sampleRate = int(options["sampleRate"])
	loopOffset = float(options["loopOffset"])

	frame   = next(_importAudio(avFile, channels, sampleRate))
	pcmData = frame.to_ndarray()

	if (align := (pcmData.shape[1] % 28)):
		pcmData = numpy.c_[
			pcmData,
			numpy.zeros(( channels, 28 - align ), numpy.int16)
		]

	# Set the loop flag at the end of the data to ensure the SPU jumps to the
	# dummy block in SPU RAM after playing the sound (if another loop point is
	# not set).
	length = pcmData.shape[1] // 28 * 16
	data   = numpy.zeros(( channels, length ), numpy.uint8)

	_loopOffset = round(loopOffset * sampleRate)
	if _loopOffset >= 0:
		endFlags = LoopFlags.LOOP | LoopFlags.SUSTAIN
	else:
		endFlags = LoopFlags.LOOP | LoopFlags.SET_LOOP_POINT

	for samples, output in zip(pcmData, data):
		SPUBlockEncoder(_loopOffset).encode(samples, output, endFlags)

	return SoundWrapper(data, sampleRate, Path(avFile.name).stem)
