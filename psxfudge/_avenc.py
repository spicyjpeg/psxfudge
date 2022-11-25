# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import math
from enum import IntFlag

import numpy, av
from ._native import SPUBlockEncoder

## Audio file importer

def _importAudio(avFile, sampleRate, channels, chunkLength = 0):
	resampler = av.AudioResampler("s16p", channels, sampleRate)
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

class LoopFlags(IntFlag):
	LOOP           = 1
	SUSTAIN        = 2
	SET_LOOP_POINT = 4

def convertAudioStream(avFile, options):
	"""
	Decodes a PyAV file object, resamples it and re-encodes it into SPU blocks
	using the given dict of options. Yields 1- or 2-tuples of bytes objects,
	depending on whether the output is mono or stereo.
	"""

	chunkLength = int(options["chunkLength"])
	sampleRate  = int(options["sampleRate"])
	channels    = int(options["channels"])
	loopOffset  = float(options["loopOffset"])

	encoder = SPUBlockEncoder(round(loopOffset * sampleRate), chunkLength)

	for frame in _importAudio(
		avFile,
		sampleRate,
		channels,
		chunkLength // 16 * 28 # Chunk length in samples
	):
		data = frame.to_ndarray()
		if align := (data.shape[1] % 28):
			data = numpy.c_[
				data,
				numpy.zeros(( channels, 28 - align ), numpy.int16)
			]

		# Set the loop and sustain flags to ensure the SPU jumps to the next
		# chunk after playing this one.
		chunk = bytearray(chunkLength * channels)
		encoder.encode(data, chunk, LoopFlags.LOOP | LoopFlags.SUSTAIN)

		yield chunk

def convertSound(avFile, options):
	"""
	Similar to encodeAudio() but deinterleaves the converted buffers,
	concatenates the left and right channel data, adds loop flags at the end
	and returns a ( data, rightChannelOffset, sampleRate ) tuple.
	"""

	sampleRate = int(options["sampleRate"])
	channels   = int(options["channels"])
	loopOffset = float(options["loopOffset"])

	frame   = next(_importAudio(avFile, sampleRate, channels))
	pcmData = frame.to_ndarray()

	if (align := (pcmData.shape[1] % 28)):
		pcmData = numpy.c_[
			pcmData,
			numpy.zeros(( channels, 28 - align ), numpy.int16)
		]

	monoLength = pcmData.shape[1] // 28 * 16
	encoder    = SPUBlockEncoder(
		round(loopOffset * frame.sample_rate),
		monoLength
	)

	# Set the loop flag at the end of the data to ensure the SPU jumps to the
	# dummy block in SPU RAM after playing the sound (if another loop point is
	# not set).
	output = bytearray(monoLength * channels)
	flags  = LoopFlags.SUSTAIN if (loopOffset >= 0.0) else 0
	encoder.encode(pcmData, output, LoopFlags.LOOP | flags)

	return output, monoLength if (channels > 1) else 0, frame.sample_rate
