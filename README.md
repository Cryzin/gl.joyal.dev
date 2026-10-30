# Gospel-Link homepage redesign

A personal homepage for Dr. Tinku Thompson (gospel-link.com).

## What's in this folder

```
gospel-link-site/
├── index.html                    The homepage
├── og-image.jpg                  Share image for WhatsApp, Facebook, LinkedIn, X (1200×630)
├── favicon.svg                   Browser tab icon
├── images/
│   ├── tinku-thompson.jpg        Portrait used in the top section
│   ├── spiritual-fitness-cover.jpg   Book cover
│   └── apple-touch-icon.png      Icon for iPhone home screens
├── gospel-link-standalone.html   Same page with images built in (easy to email or preview)
└── README.md                     This file
```

## Preview

Double-click `index.html` (keep it in this folder so the images load), or open
`gospel-link-standalone.html` anywhere.

## Going live

The current site runs on WordPress, so this is a design to rebuild there, not a
drop-in replacement. Either:

- **Rebuild in WordPress** with a block theme or page builder, using this page as the
  blueprint (fonts, colors, layout, wording), or
- **Host as a static page:** upload the whole folder (except the standalone file) so that
  `index.html` and `og-image.jpg` sit at the site root.

## Fonts (Google Fonts, free)

- **Gloock**: headlines (display)
- **Source Serif 4**: reading text (serif)
- **Schibsted Grotesk**: menus, buttons, labels (sans-serif)
- **Noto Serif Malayalam** and **Noto Serif Devanagari**: Malayalam and Hindi text

## Colors

| Name       | Hex       | Use                         |
|------------|-----------|-----------------------------|
| Pine       | `#2F6B4F` | Buttons, links, accents     |
| Pine dark  | `#234F3B` | Button hover                |
| Ink        | `#22302A` | Main text                   |
| Ink soft   | `#55625B` | Secondary text              |
| Sky        | `#EAF2F7` | Section backgrounds         |
| Meadow     | `#EDF3E9` | Section backgrounds         |
| Wheat      | `#D9A441` | Small highlights            |
| Line       | `#DDE5E0` | Dividers and borders        |

## Link previews

`index.html` includes Open Graph and Twitter tags pointing to
`https://gospel-link.com/og-image.jpg`. After going live:

1. Make sure that image URL opens in a browser.
2. Paste gospel-link.com into Facebook's Sharing Debugger
   (developers.facebook.com/tools/debug) and click **Scrape Again** to clear old previews.

On WordPress, an SEO plugin (Yoast or Rank Math) adds these tags to every page.
Set `og-image.jpg` as the default share image and the book cover for the Books page.

## Before publishing

- The page is written in Dr. Thompson's voice (first person). He should review the wording.
- Recent posts are listed by hand. Update them, or pull them in automatically when
  rebuilding in WordPress.
