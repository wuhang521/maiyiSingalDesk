# Knowledge OCR design review

Recorded 2026-10-08. Mode: Operate. Scope: ordinary extension of the existing private knowledge workspace, with a bounded confirmation review of the scored repair.

## Authority and preservation

The incumbent authority is the implemented green/neutral desktop workbench, PRODUCT.md, KNOWLEDGE-SURFACE-BRIEF.md, KNOWLEDGE-CLOUD-SURFACE-BRIEF.md and .impeccable/surfaces/knowledge-ocr.md. This pass used impeccable's document reference to record observed evidence. It does not establish a new visual world or a global design system.

Global DESIGN.md was absent at the start of the extension and at this documentation inspection. Neither DESIGN.md nor .impeccable/design.json was created. The documenter writes only this new review file and does not edit incumbent product/surface documents, application code, CSS, logos or assets. No new raster is shipped. The review screenshots are test evidence, not product assets.

At initial inspection, PRODUCT.md still said OCR was unimplemented and that real cloud credentials/models had not been jointly tested. That pre-existing drift was reported without repairing the file. During this documentation pass, a separate main-task update changed PRODUCT.md to describe the 0.5.3 consent-and-confirmation OCR scope and explicitly retain the unaccepted real paid recognition-quality limit. This documenter reread that update; it did not edit PRODUCT.md or infer permission to change global design authority.

## Evidence checked

The documenter read PRODUCT.md, both incumbent knowledge briefs, the OCR direction contract, src/components/KnowledgeWorkspace.tsx, src/components/KnowledgeOcr.tsx and src/components/knowledge-workspace.css. It also inspected the relevant inherited tokens, button controls and focus rules in src/styles.css.

All 18 named PNGs below exist, with dimensions checked from their PNG headers. The main agent and a fresh independent reviewer opened all 18 after the single repair and recapture. This documentation pass additionally opened desktop-ocr-error.png, compact-ocr-draft.png and user-2178-ocr-citations.png as spot checks. It does not represent that spot check as another complete independent review.

All files are under artifacts/knowledge-ocr/:

| State | Desktop (1440 × 920) | Compact (1080 × 700) | User size (2178 × 1401) |
| --- | --- | --- | --- |
| Unconfigured | desktop-ocr-unconfigured.png | compact-ocr-unconfigured.png | user-2178-ocr-unconfigured.png |
| Consent | desktop-ocr-consent.png | compact-ocr-consent.png | user-2178-ocr-consent.png |
| Draft | desktop-ocr-draft.png | compact-ocr-draft.png | user-2178-ocr-draft.png |
| Confirmed | desktop-ocr-confirmed.png | compact-ocr-confirmed.png | user-2178-ocr-confirmed.png |
| Citations | desktop-ocr-citations.png | compact-ocr-citations.png | user-2178-ocr-citations.png |
| Error | desktop-ocr-error.png | compact-ocr-error.png | user-2178-ocr-error.png |

The isolated preview labels its OCR text as synthetic software-test material. These captures demonstrate interface states, disclosure, text hierarchy and citation presentation; they do not measure actual recognition accuracy.

## Inherited visual and control language

The extension keeps the dark sidebar and existing identity assets, white workspace surfaces, pale green selected rows, understated separators and Chinese system typography. src/styles.css remains the source of truth for the reused surface, text, muted, border, green, amber and danger tokens. Relevant observed values include white surface (#ffffff), text (#1f2421), muted text (#59645d), primary knowledge action (#0f6e54), selection (#dff3eb), border (#d9dedb), stronger field border (#c5cdc8) and pending background (#f7edd8). These are observations of existing CSS, not newly minted tokens.

Typography inherits Segoe UI Variable Text, Microsoft YaHei UI and the existing system fallbacks. Root text is 13px; detail titles use the incumbent 24px hierarchy, section titles 15px, and body evidence 14px with a 1.85 line height and 75ch maximum width. The draft, disclosure and source text remain plain readable text rather than decorative cards.

Buttons and fields retain six-pixel corners. Primary actions inherit the shared 38px minimum height, 13px semibold labels and existing hover behavior; the knowledge workspace uses the existing darker green. The OCR file selector uses the stronger field border, ten-pixel padding and a 520px maximum width. Disabled OCR controls use the workspace's established opacity and unavailable cursor. Keyboard focus inherits the shared visible green outline; labelled consent associates with its explanatory paragraph through aria-describedby.

The library/detail layout remains minmax(270px, 33%) plus minmax(0, 1fr), with independent list/detail scrolling. Toolbar, tabs, private scope and list selection remain in place. At the existing 1180px breakpoint the detail and toolbar padding tighten; the app's supported minimum remains 1080 × 700, rather than a new mobile layout. OCR is an inline detail section after original files. It introduces no new entrance motion or shadow vocabulary.

## New draft-to-confirmation behavior

Saving an original and recognizing its content are separate states. The original stays available through the existing download row. OCR offers eligible JPG/JPEG/PNG/PDF originals up to 20 MB and reflects backend availability; an unconfigured backend explains the limitation and leaves manual text supplementation visible.

Consent starts unchecked. The disclosure says the entire selected original goes to the named provider, may incur cost, is subject to the configured PDF page limit, and will need review before Hermes retrieval. The submit button becomes available only with consent and an eligible selected file. Changing the file or submitting resets consent; busy/pending processing disables the form. A later recognition request is tucked into an explicit “重新识别原件（可能再次计费）” disclosure for an existing OCR draft or confirmed result.

The draft is named “AI 识别正文 · 待核对（暂不参与检索）”. The member can download the original, correct the body through the incumbent revision flow, or deliberately use “已核对，入库” for a current OCR draft. The adjacent guidance states that confirmation admits the current text and does not call the model again. Confirmed evidence uses the existing ready state; question results retain source buttons with page locators and revision information. OCR consent and the existing optional cloud Q&A consent remain separate choices.

A failed OCR request states that it failed without an automatic retry, preserves original access and manual correction options, and warns that another recognition may be charged again. Any retained earlier text is labelled as old text excluded from retrieval. Its failure/pending representation reuses the incumbent amber state vocabulary.

## Review outcome and repair

The initial independent disposition was “fix”. Its sole material finding was a contradictory state: the library row still said waiting while the selected detail had reached a failed OCR result.

The repair reconciles a completed detail response into its matching library row only when the detail revision and timestamp are not older than the row. Pending upload/OCR notices are also cleared when detail reaches a non-waiting state. The current source contains both protections, and the recaptured error view shows the matching terminal status and failure message in list and detail.

The main agent reports a rebuild, 64 passing tests, a passing source E2E run and recapture of all 18 views after that repair. The fresh independent reviewer confirmed the sole scored finding resolved and returned “ship”. That verdict applies only to confirmation of the scored repair; it is not a new whole-surface approval or a global design-system approval.

The automatic design detector was attempted once and could not run because its engine 0.1.5 cache was inaccessible. No automatic detector pass is claimed, and it was not retried during this documentation pass.

## Verification limits

The main agent separately reports deployment to the selected backend instance and verification through the live private signed gateway. This design record does not independently establish those checks. A screenshot, synthetic OCR result, successful build, test run or installer artifact cannot prove that the live provider accepted or accurately recognized a file.

The documented result is preservation of the incumbent interface and a visible saved-original → explicit cloud consent → excluded draft → member confirmation → citable evidence flow. Real provider recognition quality, broader deployment assurance and feature-document updates require their own evidence.
