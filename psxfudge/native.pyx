# -*- coding: utf-8 -*-
# distutils: language=c++
# cython: language_level=3, boundscheck=False, wraparound=False
# (C) 2022 spicyjpeg

from libc.stdint cimport *

import numpy
cimport numpy

cdef extern from "libimagequant.h":
	ctypedef struct liq_attr:
		pass
	ctypedef struct liq_color:
		uint8_t r, g, b, a
	ctypedef enum liq_error:
		LIQ_OK = 0
	ctypedef struct liq_image:
		pass
	ctypedef struct liq_palette:
		unsigned int count
		liq_color    entries[256]
	ctypedef struct liq_result:
		pass

	liq_attr *liq_attr_create()
	void liq_attr_destroy(liq_attr *attr)
	const liq_palette *liq_get_palette(liq_result *result)
	liq_error liq_image_add_fixed_color(liq_image *img, liq_color color)
	liq_image *liq_image_create_rgba(
		const liq_attr *attr,
		const void     *bitmap,
		int width, int height, double gamma
	)
	void liq_image_destroy(liq_image *img)
	liq_error liq_image_quantize(
		liq_image  *const input_image,
		liq_attr   *const options,
		liq_result **result_output
	)
	void liq_result_destroy(liq_result *)
	liq_error liq_set_dithering_level(liq_result *res, float dither_level)
	liq_error liq_set_max_colors(liq_attr* attr, int colors)
	liq_error liq_set_min_posterization(liq_attr* attr, int bits)
	liq_error liq_set_speed(liq_attr* attr, int speed)
	liq_error liq_write_remapped_image(
		liq_result *result,
		liq_image  *input_image,
		void       *buffer,
		size_t     buffer_size
	)

## Image quantization API (libimagequant bindings)

def quantizeImage(
	uint8_t[:, :, ::1] source,
	int    maxColors   = 256,
	object initPalette = None,
	int    targetBPP   = 8,
	float  ditherLevel = 1.0
):
	#if source.shape[2] != 4:
		#raise ValueError("source array's last dimension must be 4 (RGBA)")

	cdef liq_attr *attr = liq_attr_create()
	liq_set_speed(attr, 1)
	#liq_set_quality(attr, 0, 100)
	liq_set_max_colors(attr, maxColors)
	liq_set_min_posterization(attr, 8 - targetBPP)

	cdef liq_result *result
	cdef liq_image  *image = liq_image_create_rgba(
		attr,
		&source[0, 0, 0],
		source.shape[1],
		source.shape[0],
		0 # Use default gamma values
	)

	# Pass the given predefined palette (if any) to libimagequant.
	cdef uint8_t[:, ::1] initPaletteData
	cdef liq_color       color

	if initPalette is not None:
		initPaletteData = initPalette
		#if initPaletteData.shape[1] != 4:
			#raise ValueError("palette array's last dimension must be 4 (RGBA)")

		for index in range(initPaletteData.shape[0]):
			color.r = initPaletteData[index, 0]
			color.g = initPaletteData[index, 1]
			color.b = initPaletteData[index, 2]
			color.a = initPaletteData[index, 3]

			liq_image_add_fixed_color(image, color)

	if liq_image_quantize(image, attr, &result) != LIQ_OK:
		liq_image_destroy(image)
		liq_attr_destroy(attr)

		raise RuntimeError("image quantization failed")

	output = numpy.empty(( source.shape[0], source.shape[1] ), numpy.uint8)
	cdef uint8_t[:, ::1] outputData = output

	liq_set_dithering_level(result, ditherLevel)
	if liq_write_remapped_image(
		result,
		image,
		&outputData[0, 0],
		source.shape[0] * source.shape[1]
	) != LIQ_OK:
		liq_result_destroy(result)
		liq_image_destroy(image)
		liq_attr_destroy(attr)

		raise RuntimeError("image remapping failed")

	cdef const liq_palette *palette = liq_get_palette(result)

	outPalette = numpy.empty(( palette.count, 4 ), numpy.uint8)
	cdef uint8_t[:, ::1] outPaletteData = outPalette

	for index in range(palette.count):
		outPaletteData[index, 0] = palette.entries[index].r
		outPaletteData[index, 1] = palette.entries[index].g
		outPaletteData[index, 2] = palette.entries[index].b
		outPaletteData[index, 3] = palette.entries[index].a

	liq_result_destroy(result)
	liq_image_destroy(image)
	liq_attr_destroy(attr)

	return outPalette, output

## PS1 color space conversion

# https://github.com/stenzek/duckstation/blob/master/src/core/gpu_types.h#L135
# https://stackoverflow.com/a/9069480

cdef inline int _channelToPS1(int value):
	return ((value * 249) + 1014) >> 11
	#return ((value * value * 249) + 1014) >> 19

def toPS1ColorSpace(
	uint8_t[:, ::1] source,
	int lowerAlphaThreshold,
	int upperAlphaThreshold,
	int blackValue
):
	output = numpy.empty(( source.shape[0], ), numpy.uint16)
	cdef uint16_t[::1] outputData = output

	cdef uint16_t value

	for index in range(source.shape[0]):
		value = 0x0000

		if source[index, 3] >= lowerAlphaThreshold:
			value  = (source[index, 3] <= upperAlphaThreshold) << 15
			value |= _channelToPS1(source[index, 0])
			value |= _channelToPS1(source[index, 1]) << 5
			value |= _channelToPS1(source[index, 2]) << 10

			if not value:
				value = blackValue

		outputData[index] = value

	return output

# There probably is a way to declare functions that take n-dimensional arrays
# as input. Whatever it is, it's probably harder than copypasting the function.
def toPS1ColorSpace2D(
	uint8_t[:, :, ::1] source,
	int lowerAlphaThreshold,
	int upperAlphaThreshold,
	int blackValue
):
	output = numpy.empty(( source.shape[0], source.shape[1] ), numpy.uint16)
	cdef uint16_t[:, ::1] outputData = output

	cdef uint16_t value

	for y in range(source.shape[0]):
		for x in range(source.shape[1]):
			value = 0x0000

			if source[y, x, 3] >= lowerAlphaThreshold:
				value  = (source[y, x, 3] <= upperAlphaThreshold) << 15
				value |= _channelToPS1(source[y, x, 0])
				value |= _channelToPS1(source[y, x, 1]) << 5
				value |= _channelToPS1(source[y, x, 2]) << 10

				if not value:
					value = blackValue

			outputData[y, x] = value

	return output

## Internal low-level ADPCM encoder

# https://psx-spx.consoledev.net/cdromdrive/#cdrom-xa-audio-adpcm-compression
# https://github.com/ChenThread/candyk-psx/blob/master/toolsrc/libpsxav/adpcm.c

cdef int[5] FILTER_COEFF1 = [ 0, 60, 115,  98, 122 ]
cdef int[5] FILTER_COEFF2 = [ 0,  0, -52, -55, -60 ]

cdef cppclass FilterState:
	int s1, s2

	FilterState():
		this.s1 = 0
		this.s2 = 0

	int convolve(int _filter):
		return (
			(this.s1 * FILTER_COEFF1[_filter]) + \
			(this.s2 * FILTER_COEFF2[_filter]) + 32
		) // 64

	void update(int s0):
		this.s2 = this.s1
		this.s1 = s0

cdef cppclass ADPCMEncoder:
	FilterState filterState
	int         bitsPerSample, numFilters

	#ADPCMEncoder(int _bitsPerSample, int _numFilters):
		#this.bitsPerSample = _bitsPerSample
		#this.numFilters    = _numFilters

	int _sampleClips(int s0):
		if this.bitsPerSample == 4:
			return (s0 < -0x8) or (s0 > 0x7)
		else:
			return (s0 < -0x80) or (s0 > 0x7f)

	int _clipSample(int s0):
		if this.bitsPerSample == 4:
			return min(max(s0, -0x8), 0x7)
		else:
			return min(max(s0, -0x80), 0x7f)

	int _getShiftFactor(
		const int16_t[::1] samples,
		FilterState        &state,
		int                filterID
	):
		cdef int index, s0, s1
		cdef int minPeak = 0, maxPeak = 0, shift = 0

		# Calculate the minimum and maximum peak values for this block when the
		# given filter is applied.
		for index in range(samples.shape[0]):
			s0 = samples[index]
			s1 = s0 - state.convolve(filterID)

			minPeak = min(minPeak, s1)
			maxPeak = max(maxPeak, s1)
			state.update(s0)

		cdef int maxShift = 16 - this.bitsPerSample

		# Increment (well, actually decrement) the shift factor until no
		# clipping occurs (if the peak samples don't clip then no other samples
		# do).
		while (shift < maxShift) and (
			this._sampleClips(minPeak >> shift) or \
			this._sampleClips(maxPeak >> shift)
		):
			shift += 1

		return maxShift - shift

	uint64_t _tryEncode4(
		const int16_t[::1] samples,
		#uint8_t[::1]       output,
		uint8_t            *output,
		FilterState        &state,
		int                filterID,
		int                shiftFactor
	):
		cdef int      index, s0, s1, encoded, decoded, error
		cdef uint64_t errorSum = 0

		for index in range(samples.shape[0]):
			#s0 = samples[index] + state.error
			s0 = samples[index]
			s1 = state.convolve(filterID)

			encoded  = (s0 - s1) << shiftFactor
			encoded += 0x800 # 1 << (maxShift - 1)
			encoded  = this._clipSample(encoded >> 12)

			# Decode the freshly encoded sample back into a PCM sample (so we can
			# calculate how different it is from the original one).
			decoded = (encoded << 12) >> shiftFactor
			decoded = min(max(decoded + s1, -0x8000), 0x7fff)

			# Calculate the difference between the original and decoded samples
			# and add it to the mean square error.
			error     = decoded - s0
			errorSum += error * error
			#state.error += error
			state.update(decoded)

			# Write the appropriate nibble depending on whether the current
			# sample is odd or even (the SPU plays lower nibbles first).
			if not (index % 2):
				output[index // 2]  = <uint8_t> (encoded & 0xf)
			else:
				output[index // 2] |= <uint8_t> ((encoded & 0xf) << 4)

		return errorSum

	uint64_t _tryEncode8(
		const int16_t[::1] samples,
		#uint8_t[::1]       output,
		uint8_t            *output,
		FilterState        &state,
		int                filterID,
		int                shiftFactor
	):
		cdef int      index, s0, s1, encoded, decoded, error
		cdef uint64_t errorSum = 0

		for index in range(samples.shape[0]):
			#s0 = samples[index] - state.error
			s0 = samples[index]
			s1 = state.convolve(filterID)

			encoded  = (s0 - s1) << shiftFactor
			encoded += 0x80 # 1 << (maxShift - 1)
			encoded  = this._clipSample(encoded >> 8)

			# Decode the freshly encoded sample back into a PCM sample (so we can
			# calculate how different it is from the original one).
			decoded = (encoded << 8) >> shiftFactor
			decoded = min(max(decoded + s1, -0x8000), 0x7fff)

			# Calculate the difference between the original and decoded samples
			# and add it to the mean square error.
			error     = decoded - s0
			errorSum += error * error
			#state.error += error
			state.update(decoded)

			output[index] = <uint8_t> (encoded & 0xff)

		return errorSum

	#uint8_t encode(const int16_t[::1] samples, uint8_t[::1] output):
	uint8_t encode(const int16_t[::1] samples, uint8_t *output):
		cdef FilterState tempState

		cdef uint64_t error, bestError = UINT64_MAX
		cdef int      bestFilter, bestShift
		cdef int      shiftBase, minShift, maxShift

		for filterID in range(this.numFilters):
			# Create a copy of the filter state to ensure all encoding attempts
			# start with the same initial state.
			tempState = this.filterState
			shiftBase = this._getShiftFactor(samples, tempState, filterID)

			# Try other shift factors in a +/-1 range. Sometimes the optimal
			# shift factor is off by 1 from the one calculated by
			# _getShiftFactor().
			minShift = max(shiftBase - 1, 0)
			maxShift = min(shiftBase + 1, 16 - this.bitsPerSample)

			for shiftFactor in range(minShift, maxShift + 1):
				tempState = this.filterState

				if this.bitsPerSample == 4:
					error = this._tryEncode4(
						samples, output, tempState, filterID, shiftFactor
					)
				else:
					error = this._tryEncode8(
						samples, output, tempState, filterID, shiftFactor
					)

				# If this is the lowest mean square error so far, save the
				# parameters.
				if error < bestError:
					bestError  = error
					bestFilter = filterID
					bestShift  = shiftFactor

		# Run the encoder again with the best parameters found.
		if this.bitsPerSample == 4:
			this._tryEncode4(
				samples, output, this.filterState, bestFilter, bestShift
			)
		else:
			this._tryEncode8(
				samples, output, this.filterState, bestFilter, bestShift
			)

		return bestShift | (bestFilter << 4)

## SPU ADPCM block encoder

ctypedef enum LoopFlags:
	LOOP           = 1
	SUSTAIN        = 2
	SET_LOOP_POINT = 4

cdef cppclass SPUBlock:
	uint8_t header
	uint8_t flags
	uint8_t data[14]

cdef class SPUBlockEncoder:
	cdef ADPCMEncoder adpcmEncoder
	cdef public int   loopOffset

	def __cinit__(self, int loopOffset = -1):
		self.adpcmEncoder.bitsPerSample = 4
		self.adpcmEncoder.numFilters    = 5

		self.loopOffset = loopOffset

	def encode(
		self,
		const int16_t[::1] samples,
		uint8_t[::1]       output,
		int                endLoopFlags = 0
	):
		if samples.shape[0] % 28:
			raise ValueError("the number of samples must be a multiple of 28")

		cdef int numBlocks = samples.shape[0] // 28

		cdef SPUBlock *block
		cdef int      index, offset, channel

		for index in range(numBlocks):
			block = <SPUBlock *> &output[index * 16]

			block.flags = 0
			if index == (numBlocks - 1):
				block.flags |= endLoopFlags
			if (self.loopOffset >= 0) and (self.loopOffset < 28):
				block.flags |= LoopFlags.SET_LOOP_POINT

			self.loopOffset = max(self.loopOffset - 28, -1)

			block.header = self.adpcmEncoder.encode(
				samples[index * 28:(index + 1) * 28],
				block.data
			)
