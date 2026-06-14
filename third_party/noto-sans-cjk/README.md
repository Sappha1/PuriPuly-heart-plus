# Noto Sans CJK Medium TTC provenance

This directory stores the official bundled CJK font provenance for the native VR
Subtitle Overlay. The TTC itself lives with the application font assets at
`src/puripuly_heart/data/fonts/NotoSansCJK-Medium.ttc`; release staging,
installer output, and runtime resolver behavior are handled by packaging and
renderer code.

## Artifact

- File: `NotoSansCJK-Medium.ttc`
- Source-tree path: `src/puripuly_heart/data/fonts/NotoSansCJK-Medium.ttc`
- Upstream repository: `notofonts/noto-cjk` (GitHub may redirect to
  `googlefonts/noto-cjk`)
- Official tag/version: `Sans2.004` / Noto Sans CJK v2.004
- Upstream path: `Sans/OTC/NotoSansCJK-Medium.ttc`
- Source URL:
  `https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTC/NotoSansCJK-Medium.ttc`
- Byte length: `18,354,360`
- SHA256:
  `197d5e1e019faca33a4d55931c7d68b8056f3b97cb862049f5cb8de9efdfb8ce`
- Font metadata copyright: `© 2014-2021 Adobe (http://www.adobe.com/).`
- Font metadata license: SIL Open Font License, Version 1.1
- Font metadata license URL: `http://scripts.sil.org/OFL`

The bundled scope is the official static Medium TTC only. Do not substitute a
variable font, a generated static font, or another Noto Sans CJK artifact
without a new provenance decision.

## License and notice sources

- `OFL.txt` contains the SIL Open Font License 1.1 text from the tag-pinned
  upstream root license at
  `https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE`.
  The tag-specific `Sans/LICENSE` path returned HTTP 404 when checked on
  2026-05-26; use the tag-root license for immutable provenance.
- Upstream third-party notice source:
  `https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/README-third_party.md`

Upstream third-party notice content checked on 2026-05-26:

```text
URL: https://github.com/googlefonts/noto-cjk

Version: 1.002 or later

License: SIL Open Font License v1.1

License File: LICENSE

Note: prior releases of the CJK fonts were issued under the Apache 2
license. This was changed to the SIL OFL v1.1 starting with Version 1.002.

Description:
Noto CJK fonts, supporting Simplified Chinese, Traditional Chinese,
Japanese, and Korean. The supported scripts are Han, Hiragana, Katakana,
Hangul, and Bopomofo. Latin, Greek, Cyrillic, and various symbols are also
supported for compatibility with CJK standards.

The fonts in this directory are developed by Google and Adobe and are
released as open source under the Apache license version 2.0. The copyright
is held by Adobe, while the trademarks on the names are held by Google.

A README-formats file has been added explaining the different formats
provided and their features and limitations.
```

The upstream third-party notice is internally contradictory/stale: it identifies
the license as SIL Open Font License v1.1 and says the fonts changed to SIL OFL
v1.1 starting with Version 1.002, but later says the fonts are released under
Apache License 2.0. The vendored TTC's embedded name table and tag-pinned
`OFL.txt` license evidence identify the active license for this artifact as SIL
Open Font License 1.1.

## Verification

`SHA256SUMS.txt` records the source hash for later comparisons against staged
and installed font files. Later packaging verification should compare the app
asset font against this source hash rather than selecting another font artifact.

PowerShell verification used for this source file:

```powershell
$path = "src/puripuly_heart/data/fonts/NotoSansCJK-Medium.ttc"
$expectedSize = 18354360
$expectedSha = "197d5e1e019faca33a4d55931c7d68b8056f3b97cb862049f5cb8de9efdfb8ce"
$item = Get-Item -LiteralPath $path
$sha = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($item.Length -ne $expectedSize) { throw "Unexpected byte length: $($item.Length)" }
if ($sha -ne $expectedSha) { throw "Unexpected SHA256: $sha" }
```
