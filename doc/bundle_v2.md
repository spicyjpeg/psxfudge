
# PSXFudge bundle format (v2) specification

## Overview

The PSXFudge "bundle" format combines pre-packed textures, palettes, sounds and
arbitrary custom data into a single file that can be efficiently loaded into
memory in one shot. Bundle loader implementations can allow for one or more
bundles to be loaded at any time.

All assets contained in a bundle are indexed by a 32-bit hash of their name,
which can be calculated from a string at runtime or embedded in the code
directly as a constant (for instance by leveraging C++ `constexpr` functions).
Entry names **must** contain ASCII characters only. They can otherwise be
arbitrary, but it is recommended to stick to the following guidelines:

- adopt `lowerCamelCase` or `lower_snake_case` naming conventions, avoiding
  spaces and special characters whenever possible;
- remove superfluous file extensions from entry names;
- optionally use slash- or period-delimited paths (e.g. `stage1/map_data` or
  `menu.options.stringTable`), or add a colon-delimited prefix (e.g.
  `menu:background`), to establish a hierarchy within the bundle.

Bundles can be stored as regular files on the disc or wrapped in an archive or
container format, optionally with compression. If uncompressed, they **must** be
sector-aligned. The current version of the format does not support compression
nor specify a standard way to compress bundle data. Bundles can have any file
extension, with `.fud` being the canonical one.

## File structure

A PSXFudge bundle is made up of four sections, each of which **must** always be
aligned to the size of a CD-ROM sector (2048 bytes):

- [Index section](#index-section)
- [VRAM data section](#vram-data-section) (optional)
- [SPU RAM data section](#spu-ram-data-section) (optional)
- [Main RAM data section](#main-ram-data-section)

All entries are stored in the main RAM data section. In the case of textures and
sounds, the data stored in main RAM is a "descriptor" structure that merely
contains coordinates/pointers to the actual image or audio data in VRAM or SPU
RAM respectively.

All fields are little-endian unless otherwise specified.

## Index section

The index section starts with a 32-byte header:

| Offset | Size | Type   | Description                                               |
| -----: | ---: | :----- | :-------------------------------------------------------- |
| `0x00` |    7 | char   | Magic string, must be `fudgebn`                           |
| `0x07` |    1 | uint8  | Version number, currently 2                               |
| `0x08` |    4 | uint32 | Length of index section including padding and this header |
| `0x0c` |    4 | uint32 | Length of VRAM data section including padding             |
| `0x10` |    4 | uint32 | Length of SPU RAM data section including padding          |
| `0x14` |    4 | uint32 | Length of main RAM data section including padding         |
| `0x18` |    1 | uint8  | Number of 256x256 texture atlases in VRAM data section    |
| `0x19` |    1 | uint8  | Number of 192x256 texture atlases in VRAM data section    |
| `0x1a` |    1 | uint8  | Number of 128x256 texture atlases in VRAM data section    |
| `0x1b` |    1 | uint8  | Number of 64x256 texture atlases in VRAM data section     |
| `0x1c` |    2 | uint16 | Number of buckets in the hash table                       |
| `0x1e` |    2 | uint16 | Number of chained entries in the hash table               |

The header is then followed by a hash table listing all entries in the bundle.
The hash table is stored as an array of 16-byte structures with the following
format:

| Offset | Size | Type   | Description                                       |
| -----: | ---: | :----- | :------------------------------------------------ |
|  `0x0` |    4 | uint32 | Full hash of entry's name                         |
|  `0x4` |    4 | uint32 | Offset of entry's data within main RAM section    |
|  `0x8` |    4 | uint32 | Length of entry's data                            |
|  `0xc` |    2 | uint16 | Entry type identifier (see below)                 |
|  `0xe` |    2 | uint16 | Index of the next chained entry in the hash table |

The total number of hash table entries is the sum of the number of buckets plus
the number of chained entries. The number of buckets **must** be non-zero and a
power of 2, while there are no restrictions on how many chained entries there
can be.

Buckets (the first N entries, where N is the number of buckets) must be arranged
so that each bucket's offset in the table equals its "shortened hash", i.e. the
modulo of its full hash divided by the number of buckets. If there are no items
in the bundle whose hash satisfies this requirement, the bucket's hash field
shall be set to zero to mark it empty. For instance, a hash table with 3 items
may look like this:

```
0: hash=0x0d7f08c0 -> 0  chained=0         \
1: hash=0x94f5ed5d -> 1  chained=0         | 4 buckets
2: hash=0x71520ca6 -> 2  chained=0         |
3: hash=0x00000000     [empty]             /
```

If multiple entries end up having the same shortened hash, only one of them
(ideally the most frequently accessed one) can be a bucket. All other items must
be placed after the buckets and linked to the first one by setting chained entry
indices. Each item's chained entry index shall be the offset of the next item
with the same shortened hash (or zero if there are no more items). The example
below shows a table with 6 entries, 3 of which are chained:

```
0: hash=0x0d7f08c0 -> 0  chained=5 ------. \
1: hash=0x94f5ed5d -> 1  chained=0       | | 4 buckets
2: hash=0x71520ca6 -> 2  chained=4 --.   | |
3: hash=0x00000000     [empty]       |   | /
4: hash=0x361a4252 -> 2  chained=0 <-'   | \
5: hash=0x413e037c -> 0  chained=6 --. <-' | 3 chained items
6: hash=0x827f2b34 -> 0  chained=0 <-'     /
```

### Search algorithm

Looking up an entry in the table by its hash is simply a matter of finding the
corresponding bucket, then walking the chain until a match is found. Below is a
C/C++ implementation of the lookup algorithm.

```c++
struct [[gnu::packed]] HashTableEntry {
    uint32_t hash, offset, length;
    uint16_t type, chained;
};

const HashTableEntry *getBundleEntry(
    const HashTableEntry *hashTable, int numBuckets, uint32_t hash
) {
    // As the number of buckets is always a power of 2, "hash % numBuckets" can
    // be optimized by rewriting it as "hash & (numBuckets - 1)", which is an
    // order of magnitude faster on the PS1.
    const HashTableEntry *entry = &hashTable[hash & (numBuckets - 1)];

    if (entry->hash == hash)
        return entry;

    while (entry->chained) {
        entry = &hashTable[entry->chained];

        if (entry->hash == hash)
            return entry;
    }

    return nullptr; // Item not found
}
```

### Type identifiers

Each entry's data type is specified using a 16-bit identifier. Type IDs in the
`0x0000`-`0x7fff` range are reserved for standard types, with the following ones
currently being defined:

| Type ID  | Description                                           |
| -------: | :---------------------------------------------------- |
| `0x0000` | Unspecified data                                      |
| `0x0010` | Texture or animated texture (in VRAM)                 |
| `0x0011` | Texture or animated texture (in VRAM), interlaced     |
| `0x0020` | 15bpp background image data (in main RAM)             |
| `0x0021` | 15bpp background image data (in main RAM), interlaced |
| `0x0030` | Mono or stereo sound (in SPU RAM)                     |
| `0x0040` | String table                                          |

Custom file types should use IDs in the `0x8000`-`0xffff` range, while type ID
`0x0000` is meant to be used for one-off files such as structured data specific
to a game.

### Hash function

The hash function used by PSXFudge is based on the
[`sdbm`/`gawk` hashing algorithm](http://www.cse.yorku.ca/~oz/hash.html), chosen
for its relatively low collision rate (only slightly worse than FNV-1a on short
strings) and performance. A C/C++ implementation of this function is provided
below.

```c++
/* Runtime implementation */

uint32_t calculateHash(const char *str) {
    uint32_t value = 0;

    while (*str)
        value = ((uint32_t) *(str++)) + (value << 6) + (value << 16) - value;

    return value;
}

/* Compile-time implementation (C++) */

constexpr static inline uint32_t calculateHash(
    const char *const str, size_t maxLength = -1, uint32_t value = 0
) {
    if (*str && maxLength)
        return calculateHash(
            &str[1], maxLength - 1,
            ((uint32_t) *str) + (value << 6) + (value << 16) - value
        );

    return value;
}

// Add "xyz"_h as a shorthand for calculateHash("xyz")
constexpr static inline uint32_t operator""_h(
    const char *const str, size_t length
) {
    return calculateHash(str, length);
}
```

## VRAM data section

The VRAM data section contains zero or more texture pages, each of which is a
64x256x15bpp raw RGB image containing packed images and color palettes, grouped
into texture atlases, with each atlas containing up to 4 pages. Even though the
size of a texture page is fixed, the width of an atlas can vary as the PS1 GPU
allows some texture types to cross texture page boundaries:

- 4bpp indexed color images and their palettes cannot cross page boundaries;
- 8bpp indexed color images may cross a single boundary and span up to 2 pages;
- 15bpp RGB images may cross up to 3 boundaries and span up to 4 pages;
- palettes for 8bpp images are always 256 pixels wide and span 4 pages.

The bundle format groups pages that **must** be horizontally contiguous in VRAM
once loaded into a single atlas. Texture pages are always laid out in the VRAM
data section in the following order:

- pages belonging to 256x256 (4-page) atlases;
- pages belonging to 192x256 (3-page) atlases;
- pages belonging to 128x256 (2-page) atlases;
- pages belonging to 64x256 (single-page) atlases.

The number of atlases of each type is stored in the bundle header. The total
number of pages contained in a bundle is thus given by:

```
count = (number of 256x256 atlases * 4) + (number of 192x256 atlases * 3) +
    + (number of 128x256 atlases * 2) + number of 64x256 atlases
```

### Optimal VRAM layout

The placement of framebuffers and other buffers within VRAM shall be optimized
to allow for as many 256x256 atlases as possible to be allocated simultaneously.
For instance, the typical PS1 VRAM layout that places framebuffers vertically on
the left side of VRAM is not ideal if the framebuffers are not 256 or 512 pixels
wide, as space that could otherwise be available for a 256x256 atlas will end up
being fragmented. Such a layout, with a double 320x240 framebuffer and two
fragmented 192x256 spaces, is shown below:

```
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|   320x240    |  256x256  |  256x256  |192x256 |
| framebuffer  |   atlas   |   atlas   | atlas  |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|   320x240    |  256x256  |  256x256  |192x256 |
| framebuffer  |   atlas   |   atlas   | atlas  |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
```

The optimal and recommended layout is to have framebuffers placed side-by-side,
maximizing the number of contiguous pages available for atlas loading:

```
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|   320x240    |   320x240    |  256x256  |128x |
| framebuffer  | framebuffer  |   atlas   | 256 |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
|  256x256  |  256x256  |  256x256  |  256x256  |
|   atlas   |   atlas   |   atlas   |   atlas   |
+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
```

## SPU RAM data section

The SPU RAM section contains zero or more SPU-ADPCM encoded audio samples, each
of which is made up by a series of 16-byte blocks. Each sample is terminated by
setting its last block's loop flags (second byte) to one of the following
values:

- `0x05` if the sample shall only be played once;
- `0x03` if the sample should loop until manually stopped.

## Main RAM data section

The main RAM section holds the contents of each entry listed in the bundle's
hash table. An entry's data may either be entirely self-contained in this
section or act as a "descriptor" and reference data in the VRAM or SPU RAM
sections.

The structure of all standard entry types defined by this specification is
described below. For custom entry types, the data can be arbitrary (and may in
fact be generated by an external tool before being embedded into the bundle).

Each entry's data **must** be aligned to at least 4 bytes regardless of its
type; entries that contain executable code such as relocatable overlays *should*
be aligned to 16 bytes to match the alignment of the PS1's instruction cache.
When loading a bundle, the area allocated for main RAM data sections *should* be
aligned to 16 bytes in order to preserve the alignment of such entries.

### Texture descriptor

A static, mipmapped and/or animated texture is stored as a series of frames
packed into VRAM texture pages and described in the main RAM section using an
8-byte header:

| Offset | Size | Type   | Description             |
| -----: | ---: | :----- | :---------------------- |
|  `0x0` |    2 | uint16 | Texture width           |
|  `0x2` |    2 | uint16 | Texture height          |
|  `0x4` |    2 | uint16 | Number of frames        |
|  `0x6` |    2 | uint16 | Number of mipmap levels |

The header is followed by a 2- or 3-dimensional array of 16-byte frame
descriptors, one for each interlaced field of each mipmap level of each frame.
Given an animation frame index, mipmap level and interlaced field, the
respective index into this array is computed as:

```
if non-interlaced:
    index = (frame index * number of mipmap levels) + mipmap level
if interlaced:
    index = (frame index * number of mipmap levels * 2) + (mipmap level * 2) +
        + (0 if field == even, 1 if field == odd)
```

Each frame descriptor is a structure of the following format:

| Offset | Size | Type   | Description                                      |
| -----: | ---: | :----- | :----------------------------------------------- |
|  `0x0` |    2 | uint16 | Index of the texture page the image is in        |
|  `0x2` |    2 | uint16 | Index of the texture page the palette is in      |
|  `0x4` |    1 | uint8  | X offset of image within texture page            |
|  `0x5` |    1 | uint8  | Y offset of image within texture page            |
|  `0x6` |    1 | uint8  | Left margin (X offset of frame within texture)   |
|  `0x7` |    1 | uint8  | Top margin (Y offset of frame within texture)    |
|  `0x8` |    1 | uint8  | Frame image width (excluding margin)             |
|  `0x9` |    1 | uint8  | Frame image height (excluding margin)            |
|  `0xa` |    2 | uint16 | Packed X/Y offset of palette within texture page |
|  `0xc` |    4 | uint32 | Frame flags (see below)                          |

All X offsets and widths are in image pixel units, not VRAM pixels (e.g. a
16-pixel-wide 4bpp frame shall have its width stored as 16, even though its
respective image in VRAM would only span 4 pixels horizontally as VRAM is a
15bpp buffer). The palette offset is a packed value calculated as:

```
palette offset = (palette X within page / 16) | (palette Y within page << 6)
```

The texture page index is the offset at which the page containing the image or
palette can be found in the VRAM data section, ignoring texture atlas grouping.
For instance, if an image is in the second page of a 3-page atlas found in the
VRAM data section after two 4-page atlases, the resulting page index will be 9
(4 + 4 + 1). If the image or palette spans multiple pages, the index of the
first/leftmost page is used.

A frame may be smaller than the texture it belongs to. In such case, the frame
should be padded to the width and height specified in the texture header with a
transparent margin. The box model of a frame is pictured below:

```
+-- Texture ------------------+--
|        ^                    | ^
|        | Top margin         | |
|        v                    | |
|      +-- Frame --------+    | |
|<---->|                ^|    | | Texture height
| Left |   Frame height ||    | |
|margin|                v|    | |
|      +-----------------+    | |
|      |<- Frame width ->|    | v
+-----------------------------+--
|<------ Texture width ------>|
```

Note that the size of each frame excluding any margins is constrained to 255x255
due to the GPU's texture page limitations. Margins are also limited to up to 255
pixels on each side, thus the maximum allowed size of a texture is 765x765.

Frame flags are a bitfield containing the following bits:

| Bits | Description                                                            |
| ---: | :--------------------------------------------------------------------- |
|  0-1 | Color depth (0 = 4bpp indexed, 1 = 8bpp indexed, 2 = 15bpp RGB)        |
|  2-3 | Interlaced field (0 = no interlacing, 1 = even, 2 = odd)               |
|    4 | Margin flag (1 = frame is smaller than texture and must be padded)     |
|    5 | Flip flag (1 = texture is rotated 90 degrees counterclockwise in VRAM) |
| 6-31 | Reserved                                                               |

Support for padded and flipped frames is optional in loader implementations, as
they are only practical for certain types of images. A loader may choose not to
support these features and instead throw an error if the respective flags are
set, and packers shall not use them unless explicitly enabled.

### Sound descriptor

A single 8-byte structure is used to describe each audio sample in the SPU RAM
data section:

| Offset | Size | Type   | Description                                                         |
| -----: | ---: | :----- | :------------------------------------------------------------------ |
|  `0x0` |    2 | uint16 | Offset of left channel data within SPU RAM section in 8-byte units  |
|  `0x2` |    2 | uint16 | Offset of right channel data within SPU RAM section in 8-byte units |
|  `0x4` |    2 | uint16 | Length of each channel's data in 8-byte units                       |
|  `0x6` |    2 | uint16 | Sampling rate multiplied by `(0x1000 / 44100)`                      |

For mono sounds, both the left and right data offsets shall be set to the same
value.

### String table

String tables are stored entirely in main RAM and make use of a hash table
similar to the one found in the bundle header. The table starts with a 4-byte
header:

| Offset | Size | Type   | Description                                 |
| -----: | ---: | :----- | :------------------------------------------ |
|  `0x0` |    2 | uint16 | Number of buckets in the hash table         |
|  `0x2` |    2 | uint16 | Number of chained entries in the hash table |

The header is followed by one or more buckets and zero or more chained entries,
represented through an 8-byte structure:

| Offset | Size | Type   | Description                                       |
| -----: | ---: | :----- | :------------------------------------------------ |
|  `0x0` |    4 | uint32 | Full hash of the string's key                     |
|  `0x4` |    2 | uint16 | Offset of the string within the string blob       |
|  `0x6` |    2 | uint16 | Index of the next chained entry in the hash table |

The actual strings referenced by the table are concatenated into a single "blob"
which is placed after the last hash table entry. Each string **must** be
terminated with a null byte, as its length is not stored in the table.

-----------------------------------------
_Last updated on 2023-04-30 by spicyjpeg_
