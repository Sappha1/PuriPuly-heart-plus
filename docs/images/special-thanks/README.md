# Special Thanks Avatar Images

This directory is for manually collected Special Thanks avatar images.

## Image requirements

- Use a 1:1 square image.
- Prefer 512x512 pixels. Use 256x256 pixels as the minimum accepted size.
- Prefer WebP for repository storage. PNG or JPG source images are fine when collecting images from people.
- Keep repository WebP files reasonably small, ideally under 100KB when practical.
- Crop around the VRChat avatar face or upper body.
- Keep enough margin around hair, ears, and accessories so the image still reads well when the README displays it as an 88x88 circular avatar.
- GitHub README sanitizes inline CSS, so do not rely on `border-radius` in Markdown. Store final repository avatars as circular-cropped images on a square transparent canvas when the README should show a circle.
- Keep the image safe to display publicly in the GitHub README.

## Consent requirements

Only add an image when the person has agreed that their display name and image may appear publicly in this repository README.

If the person provides a profile link, use one public profile URL only. Examples include X/Twitter, VRChat, GitHub, YouTube, Booth, Bluesky, or a personal website.

## all-contributors URL requirement

The stock `all-contributors-cli` requires `avatar_url` to be an absolute URL. Do not put relative paths such as `docs/images/special-thanks/name.webp` in `.all-contributorsrc`.

After images are committed, use absolute raw GitHub URLs in `.all-contributorsrc`, for example:

```text
https://raw.githubusercontent.com/kapitalismho/PuriPuly-heart/main/docs/images/special-thanks/sui_32c.webp
```

During preview work, keep using absolute placeholder URLs such as `https://placehold.co/...`.
