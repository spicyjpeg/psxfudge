
# PSXFudge

**NOTE**: _as this is a work-in-progress project, there are no PS1-side_
_libraries for loading the bundle files and other custom formats generated by_
_PSXFudge yet. This makes these tools (with the exception of the .TIM_
_converter perhaps) mostly useless to people other than myself. - spicyjpeg_

PSXFudge is a set of command-line tools and libraries for converting assets and
data to PlayStation 1-compatible formats.

It is not meant as a replacement for the tools included in Sony's SDK or other
toolkits, but rather as a completely different asset pipeline that brings the
PS1 closer to modern game development. For example, while the traditional PS1
development workflow involves using a GUI to manually lay out textures in VRAM,
PSXFudge's `fudgebundle` uses an automated packer and generates texture pages
ahead of time, making texture management at runtime unnecessary. A few tools,
such as a .TIM converter with built-in quantizer, are still provided for ease
of development.

Current features include:

- Custom unified "bundle" format that combines packed textures, sounds and
  arbitrary data into a single file that can be quickly loaded into memory,
  replacing the multitude of formats used by the official SDK
- Hash-table-based asset indexing by name (no more hardcoded IDs and enums!)
- Support for importing and converting Adobe Animate spritesheets
- Full texture pipeline from conversion to 4/8bpp (powered by `libimagequant`)
  to packing into 64x256 texture pages
- Standalone .TIM image quantizer and converter

The following features are planned for a future release:

- PSn00bSDK integration to allow bundle and stream files to be used without
  external libraries or additional code
- Custom archive format based on the same hash table as the bundle format
- Custom FMV format with p-frames and motion vectors (should yield higher video
  quality compared to the "standard" .STR format)
