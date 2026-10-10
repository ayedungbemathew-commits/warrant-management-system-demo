# Change log

## 12.7.3 (2026-10-07)
**Amount-entry, warrant-type, and upgrade-backup safeguards**
- Refuse malformed comma grouping such as `1,25` or `12,34,567` instead of silently turning it into a different Naira amount.
- Restrict configured warrant types to the two types whose legal wording and PDF layouts are supported. Saved Capital warrants remain printable if current settings list only Recurrent.
- Require a pre-upgrade backup when the one-time catalogue repair is pending, including catalogues without saved warrants.
- Added regression tests for each case.

## 12.7.2 (2026-10-07)
**Old warrants can always be reprinted; safer start-up; input hardening**
- **Saved warrants no longer become unprintable when the MDA / code tables or Settings change.** Previously, recoding an MDA, deleting an economic code or MDA, changing the Warrant No. format, or editing the code-length rules made every older warrant fail with "code not found" (or similar) on Print, PDF, Share and the weekly export. A saved, unchanged warrant is now checked only for what affects the printed page (date, year, number present, MDAs, payees, amounts). Saving a new or edited warrant still uses the full check.
- The Change-MDA-code and Delete-MDA messages now say what really happens to saved warrants.
- **Start-up no longer fails when the "before upgrade" backup cannot be made** (full disk, unwritable folder): the program starts and shows the reason. It still stops if a one-time data conversion is waiting, because that must not run without a safety copy.
- Column widths are limited to a sane maximum and non-finite values are ignored, so one typo cannot break the preview and every PDF.
- Amounts typed as `1.250,50`, `1_000` or `1e3` are refused instead of being silently misread.
- The state name in the authorization paragraph is escaped like every other printed text.
- New regression tests for all of the above.

## 12.7.1 (2026-10-06)
**Guard against missing or repeated schedule lines during PDF pagination**
- Before a PDF is saved, the renderer checks that every MDA line was drawn once, in order, and that each printed part-total matches its underlying lines. If pagination ever drops or repeats a range, the PDF is refused with a clear message instead of producing a misleading warrant.
- Added a regression that reads the generated multipage PDF and confirms serial numbers and all line markers continue without gaps, plus a simulated missing-range rejection check.

## 12.7.0 (2026-10-06)
**Dates are DD/MM/YYYY**
- Every warrant date is now written **DD/MM/YYYY** (`04/10/2026` = 4 October 2026) in the entry form, Print Preview, PDFs, saved list, shared files, weekly exports and the backups list. Typing `4/10/26` or `04/10/2026` is tidied automatically; a date such as `10/25/2026` (month 25) is refused instead of being silently swapped.
- **Your saved warrants are converted once, automatically, at the first start** (a backup named *before_upgrade* is made first). Databases written by 12.5 - 12.6 (month first) are converted to day first; a pre-12.5 database was already day first and is only tidied. Restoring an old month-first backup converts it too. Warrant Nos. are never changed.
- **Warrant No. keeps its order `XXMMDD/YY`** (month, then day): 4 October 2026 with digits 99 is still `991004/26`. Numbers already issued are identifiers and are not rewritten.
- **Shared warrant files:** new files are format version 2 (day first). Version-1 files from 12.5 - 12.6 are converted when opened; an older program refuses a version-2 file with an "update this program" message instead of misreading its dates.
- Weekly-export and *Date* screens, messages and labels say DD/MM/YYYY. 12 new automated tests cover every upgrade path (59 in total).

## 12.6.0 (2026-10-06)
**Master release: full code and data review, crash fixes, safer backups, and the missing automated tests**
- **PDF layout:** a large *Row padding* (about 10 pt or more) no longer makes the whole warrant fail to lay out; horizontal padding is limited to what the narrowest column can hold. Normal settings produce exactly the same pages as before.
  A single line taller than a page now gives a clear message. PDFs are written to a temporary name and moved into place only when complete, so a failed export can never leave a broken file or overwrite a good one.
- **Amount in words** works up to the limit of ₦999 trillion (it used to fail from ₦1 trillion upward, which broke every refresh), and a zero total reads "Zero Naira". Amounts must be finite numbers below ₦1 quadrillion everywhere (screen, saved lists, shared files).
- **Start-up:** "System (auto)" theme no longer fails on Windows in light mode. If ReportLab is missing the program says so instead of stopping with an error. The data folder falls back to your own user folder when ProgramData is not writable, and is opened to all local users when first created.
- **Backups:** backup folders with `#`, `%`, `?` or spaces in their names now verify correctly (previously every backup there failed).
- **MDA / economic-code data repaired once at start-up (nothing deleted, no code renamed):** 33 economic codes filed under the wrong MDA (32 under `125009001` instead of `0125009001`, one APIC code) are moved to the MDA their code names; blank MDA names are filled; double and trailing spaces are removed. The bundled database is already repaired.
- **Saved items** now follow their MDA when its code is changed and are removed when the MDA or the economic code is deleted.
- Restore and Import settings refresh the remembered UPPERCASE option; migration markers are no longer exported with settings.
- Settings checks the Warrant No. format (valid pattern, example matches) before saving. The Add-line dialog uses the same amount rules as saved items (accepts N / ₦ / commas; refuses nan / inf).
- Changing the global page / table layout no longer makes the open warrant look "unsaved". Accepting a sender's text formatting keeps both warrant types in step.
- Saved-warrants search waits a moment after typing instead of searching on every key. Print copies and the preview file are cleaned up. The database waits up to 20 s for another window instead of failing at once.
- **Warrant wording made consistent:** Recurrent no longer prints `Kobo.(₦44,455.25)Only` (stray full stop, no spaces) but `Kobo (₦44,455.25) Only, being part of ...`, the same punctuation as Capital; Capital now reads `Naira and Thirty-Two Kobo`, as Recurrent already did.
- **Build:** the `tests/` folder (works on Windows and Linux) required by *Build Installer.bat* and *Run Tests.bat* is included again (it was missing from 12.5.2, so the installer could not be built). Portable build now bundles the change log; `pythonw` start passes the shared-file argument.

## 12.5.2 (2026-10-05)
**Shared text formatting across Recurrent and Capital warrants**
- Text sizes, bold choices, paragraph styling, and fonts changed in Format now apply to both warrant types and persist together. Existing per-type text preferences are consolidated to one shared style at startup; Reset restores shared text styling for both.
- Page and table layout choices now apply to all warrants, including weekly exports and previously saved warrants. Capital and Recurrent keep separate column proportions so their different schedule codes remain legible.

## 12.5.1 (2026-10-05)
**Payee memory reliability, full-year dates, and continuation-page stamp clearance**
- Newly accepted payees are remembered as soon as a valid line is added; the suggestion cache also refreshes to pick up changes made by another running copy.
- Warrant dates now display and save as **MM/DD/YYYY** throughout forms, PDFs, saved records, shared files, weekly exports, and backups. Existing V12.5 short-year dates and older day-first dates are upgraded once; Warrant No. values keep their established two-digit year suffix.
- Continuation-page Warrant No. text is shifted left to leave a clear area at the right for a timestamp.
- Keeps the Carbon theme and theme choices introduced for this release line.

## 12.5.0 (2026-10-05)
**Shareable editable warrant files, date-based Warrant No., MM/DD/YY dates**
- **Share Warrant / Open Shared:** a completed warrant is saved as one `.wmsw` file that can be sent by WhatsApp, e-mail, USB ... Another WMS opens it (button, or double-click with the installed version),
  and the recipient can edit, save and print it. It carries the whole warrant: header, number, date, every MDA and line (codes, payees, amounts), totals, table / page / letterhead formatting, text formatting and the UPPERCASE-payees option.
- **No duplicates:** every warrant now has a permanent identity (uid). Opening a file saves nothing; a warrant you already have is found by its identity (same content: your copy opens; changed: you choose the file's version - which updates your
  existing record - or your own copy). A shared warrant can be saved only once. A different warrant with the same Warrant No. is detected and must be renumbered first.
- Missing MDAs / economic codes on the other computer are listed and can be added (MDA password required); differing text formatting is offered, never applied silently. Damaged, cut-off or altered files are refused (checksum, totals re-checked).
  The file is not encrypted. Installer: `.wmsw` files open in the Warrant System.
- **Warrant No. = XXMMDD/YY** (e.g. `991004/26`): you type or pick only the first two digits; month, day and year come from the warrant date. The number of a saved warrant never changes by itself - opening it on another day or computer
  shows it exactly as saved, and the computer's date is used only to start a brand-new warrant. Changing a saved warrant's date asks first; "Update from date" and changing the two digits rebuild it on purpose. The next free pair is offered when a number is taken.
- **Dates are MM/DD/YY everywhere** (screen, preview, PDF, saved list, weekly export, backups list). Typing `10/4/26` or `10/04/2026` is tidied automatically. Records saved by older versions are converted once at start-up
  (backup first; Warrant Nos. untouched).
- New files `wms_dates.py`, `wms_share.py`; automated tests in `tests/test_dates_numbers.py` and `tests/test_shared_warrant.py`.

## 12.4.1 (2026-10-02)
**Forget Payee is back in Add Line, and an UPPERCASE option for every Beneficiary / Payee**
- **Forget This Payee** now appears in the *Add Line* dialog (next to Add), as well as in the edit-line dialog. You can also open the payee suggestions, highlight a name and
  press **Delete** to forget just that one. Saved warrants are never changed by forgetting a name.
- **UPPERCASE payees** option: tick it on the Entry tab (quick switch) or in Settings. Then every Beneficiary / Payee is written in capitals: while you type or paste,
  in the suggestions, in the schedule on screen, in Print Preview, in the PDF / print-out, and in the weekly PDF export - including warrants saved earlier in mixed case.
- Switching it on never rewrites stored names or saved warrants, so switching it off brings every name back exactly as it was typed. Turning the option on or off is not
  treated as a change to the warrant (no "unsaved changes" prompt). Names that differ only by capitals appear once in the suggestions.

## 12.4.0 (2026-10-02)
**Automatic weekly PDF export**
- Every warrant created in a week is exported as a PDF into a folder you choose. Switch it on in **Weekly Export** (toolbar) or Settings > Weekly PDF export.
- When a week has finished it gets its own folder (for example `Week_2026-09-28_to_2026-10-04`) with one PDF per warrant and an `Export_Summary.txt`
  listing what was exported and anything that could not be (and why). You choose the day a week starts on, and whether warrants are picked by the date they
  were saved (default) or by the date printed on them.
- The PDFs are made by the same engine, with the same layout and the same checks (including the Recurrent / Capital code rule) as Export PDF. A warrant that fails
  the checks is left out and reported; the others are still exported.
- Runs inside the program (shortly after start-up, then every hour, in the background so the screen never freezes). Weeks that finished while the program was
  closed are exported the next time it runs. A finished week is exported once; a disk / folder problem is retried automatically.
- Optional Windows schedule (button in the dialog): Windows starts the program without a window every week, exports, and exits - so it also works when nobody
  has the program open. `Run Weekly Export.bat` does the same by hand. Command line: `--weekly-export`.
- **Export now**: any date range, last week, or this week so far, whenever you want. It never changes the automatic schedule.
- Nothing in your folder is ever deleted; exporting a week again overwrites the PDFs of the same name. The folder and progress are stored on this computer and are not
  part of exported settings files.
- New file `wms_weekly.py` (with automated tests in `tests/test_weekly_export.py`).

## 12.3.2 (2026-10-01)
**Mouse wheel works everywhere**
- Turn the wheel anywhere in the program and the thing under the mouse scrolls: the MDA list on the Entry tab (also when the mouse is over the header fields), the Print Preview, the
  Format Settings panel, Saved Warrants, pick-lists, the payee suggestions, Settings, Backups and the About log. Over a scrollbar, the list beside it scrolls.
- If a table is already at its end (or fits on screen) the page scrolls instead, so the wheel never gets "stuck" on a small table. Shift + wheel scrolls sideways.
- The wheel no longer changes a drop-down or a number box by accident (Warrant Type, Year, font sizes, ...); it scrolls the page.
- While a pop-up window is open, the window behind it is left alone.
- New file `wms_wheel.py` (with automated tests in `tests/test_mousewheel.py`); the old partial wheel code was removed.

## 12.3.1 (2026-10-01)
**Add Line is now the fast way (simpler)**
- Press **Add Line**, type or search the economic code and the description fills in by itself; then the **Add beneficiary and amount** box lets you add as many payees and amounts as you need under that
  code and description (type the payee, Tab, type the amount, press Enter, next payee). **Save Lines** puts them all on the warrant. Payees use the same autocomplete as before.
- Optional: tick *Remember this code and description for this MDA* and next time pick it from **Saved items** at the top of the same window. Saved items are per MDA, stored in the database,
  included in backups and Export / Import, never overwritten or duplicated, and only offered on warrants of the matching Recurrent / Capital type. Manage them in Settings > Saved codes & descriptions.
- The Recurrent / Capital rule still applies as before (a wrong-type code is flagged as soon as it is entered and cannot be added).
- The separate Quick Add window from 12.3.0 was removed.

## 12.2.3 (2026-10-01)
- Fonts are now ONE setting for every warrant: choosing a font applies to Recurrent and Capital warrants, old and new, so warrants never end up in different fonts.
  Fonts saved earlier per type are merged on first start. "Reset text styling" no longer touches fonts. Sizes work exactly as before (Auto, or a size per warrant type).

## 12.2.2 (2026-10-01)
- Total row and Grand Total row now have separate font and size settings (Format Settings > Fonts). Existing settings carry over to both until you change one.

## 12.2.1 (2026-10-01)
- Taskbar icon: the program now gives its taskbar button its own identity (app id, icon, name and relaunch command) through Windows, so the shield shows instead of Python's logo,
  including on a pinned button. Each step is written to the log (`taskbar identity ...`, `taskbar properties ...`).

## 12.2.0 (2026-10-01)
**Fonts**
- Every part of the warrant has its own font and optional size: date, title, ref line, recipient block, Authorization paragraph, PROVIDED paragraph, AND paragraph, MDA names,
  column headings, schedule table, Total / Grand Total rows, the running WARRANT NO on pages 2+, and "Page x of y" (Format Settings > Fonts). "Use one font for every part" sets them all at once.
- Fonts offered: Times New Roman, Calibri, Arial, Courier New (built-in look-alikes are bundled, so they work on any PC), plus Cambria, Georgia, Verdana, Tahoma, Segoe UI, Trebuchet MS,
  Palatino Linotype, Book Antiqua, Bookman Old Style, Garamond, Century Gothic, Candara, Constantia, Corbel and Consolas when Windows has them. A font without the Naira sign is never offered.
- A font that is not installed on a PC falls back to Times New Roman (and is noted in the log) instead of breaking printing.

**Logo and icon**
- New WMS shield logo: header, About window (full logo), installer / program icon, Dark-theme badge version.
- Taskbar and title-bar icon fixed: a classic-format icon is set through Tk AND directly through Windows (works even when Pillow is missing); any failure is written to the log.
  After updating, delete old shortcuts and run `Create Desktop Shortcut.bat` again (Windows caches the old icon).

## 12.1.0 (2026-10-01)
**Reliability**
- Saving a warrant is one database transaction. Editing an old warrant updates the same record (no duplicate), keeps the replaced version, reads the record back and shows it.
- If a save fails the warrant is NOT reported as saved; the reason is shown and logged. If someone else changed the warrant meanwhile, the save is refused instead of overwriting.
- Old warrants stay searchable by the Warrant No. they were first saved under.

**Validation**
- Recurrent / Capital check by economic-code length: 18 digits = Recurrent, 30 digits = Capital. A wrong-type code blocks adding the line, changing the type, Save, Validate, PDF and Print.
- Warrant No. must be 6 digits / 2 digits (e.g. 990930/26); anything else is "not correct" with the reason.
- The Validate button now reports "Validation passed".

**Speed and layout**
- Payee box: instant type-ahead from memory; remembers new payees; ignores capitals/extra spaces.
- Letterhead space: 10 lines on page 1 (default). Pages 2+ unchanged, WARRANT NO on the first row.
- Amount in words: every word capitalised except "and", on screen and on both warrant types.

**Data protection**
- Automatic verified backups at start-up and before every upgrade; Backups & restore window; export / import of settings, rules, MDAs and payees.

**Fit and finish**
- Version number, this change log, an About & log window, a rotating error log (`logs\wms.log`), a Windows installer script and a permanent automated test suite.

## 12.0
- Original release (dynamic MDAs, economic codes, payees, format controls, PDF and direct printing).

