# Accessibility statement (template)

> **Template for the person or organisation that runs this Heardloop instance.** Replace every `[fill in]` and
> keep the "known gaps" list honest. Whether a statement is required, and what it must contain, depends on
> who runs the service and for whom; check with whoever is responsible for that. This is not legal advice.
>
> Hebrew version: [docs/he/ACCESSIBILITY.md](he/ACCESSIBILITY.md)

Last updated: [fill in date] · Heardloop version: [fill in, see Settings -> About]

## Standard

The interface aims at **WCAG 2.1 level AA**, which is also the basis of the Israeli standard IS 5568.

## What has been done

- English and Hebrew interfaces with the correct reading direction (right-to-left for Hebrew); the page
  language and direction follow the chosen language.
- Every screen works with the keyboard; focus is visible; dialogs move focus inside, close with Escape and
  return focus to the button that opened them.
- A "Skip to content" link; after moving to another screen, focus goes to its heading. The address bar
  follows the screen, so Back, Forward and reload work.
- Form fields have visible labels; errors and confirmations are announced to screen readers.
- Text contrast of at least 4.5:1 in the light and the dark theme, for every accent colour on offer
  (checked automatically on every change).
- Adjustable text size (Settings -> Appearance), light and dark themes; layouts reflow on narrow screens,
  and buttons are at least 24 px (44 px on touch screens).
- The post editor warns when "styled" Unicode letters would make a post hard to read for screen readers.
- Rescheduling by drag and drop in the calendar has keyboard alternatives: Shift + arrow keys on a focused
  post (a day or a week), or the date and time fields in the post editor. A move that is not allowed is
  explained.
- Information is not given only on hover: publish failure reasons and calendar post details are shown as text
  and read by screen readers.
- Leaving the post editor with unsaved changes asks first.
- Animations are switched off when the system asks for reduced motion.

## Known gaps

- No full manual audit with a screen reader has been recorded yet. [fill in date and result when done]
- The trend line in Reports is a picture; its exact numbers are in a table right below it ("Show the numbers
  as a table"), in the totals above it and in the CSV export.
- [fill in anything you found]

## Contact

If something is hard to use, tell [fill in name, e-mail and phone of the accessibility contact].
We aim to answer within [fill in] working days.
