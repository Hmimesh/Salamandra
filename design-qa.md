# Salamandra Visual QA

## Comparison Target

- Source visual truth: `C:\Users\highs\Downloads\ChatGPT Image Sep 3, 2026, 02_46_45 PM.png`
- Original brand source: `C:\Users\highs\AppData\Local\Temp\codex-clipboard-3ea47bc8-82eb-450f-8a38-69d6563efd55.png`
- Browser-rendered implementation: `docs/design-qa-2026-09-03/landing-1536x1024.png`
- Full comparison: `docs/design-qa-2026-09-03/reference-comparison.png`
- Focused comparison: `docs/design-qa-2026-09-03/focused-comparison.png`
- Route and state: unsigned `/` landing page, Plan selected, light empty test workspace shown in the product preview
- Viewport: 1536 x 1024 CSS pixels; device pixel ratio 1
- Source pixels: 1536 x 1024
- Browser capture pixels: 1521 x 1014 after the browser scrollbar and capture gutter
- Density normalization: the implementation capture was resized to 1536 x 1024 for the comparison canvas; no density scaling was applied to the source

## Findings

No actionable P0, P1, or P2 differences remain. The implementation follows the supplied mockup as visual direction while omitting unsupported mock content.

- Fonts and typography: the system sans stack preserves the mockup's compact B2B hierarchy, strong display weight, zero letter spacing, and readable body copy. Mobile wrapping remains deliberate at 360px.
- Spacing and layout: the dark hero, integrated product proof, light workflow band, dark operations band, warm cream call to action, and dark footer preserve the reference rhythm. The implementation is taller because the real workflow content remains readable instead of compressing a full page into a single mockup frame.
- Colors and tokens: Salamandra yellow, near-black/navy, white, and restrained teal are consistently mapped. The final call to action uses one controlled cream-to-white gradient from the supplied mockup direction so the yellow mark retains contrast.
- Image quality: the generated salamander mark remains recognizable at 24px through header size on light and dark backgrounds. The product preview is a browser capture of an empty local test workspace, not invented product data.
- Copy and content: all copy describes implemented Salamandra behavior. No mockup metrics, customers, testimonials, pricing, integrations, or unsupported claims were copied.
- Icons and controls: Lucide icons are consistent in weight and alignment. Product and workflow navigation, all six workflow tabs, mobile navigation, primary calls to action, and legal links are functional.
- Accessibility: landmarks and heading order are coherent; the workflow exposes tab semantics and selected state; arrow, Home, and End keys work; focus styles are visible; reduced-motion users receive no animated section scroll.

## Full-View Evidence

The combined comparison shows the same primary hierarchy: branded dark header, bold left-aligned value statement, real product workspace as the dominant visual, a six-stage workflow immediately after the hero, and strong alternating section contrast. The implementation intentionally gives the real application screenshot and workflow copy more vertical room than the generated reference.

## Focused Evidence

The focused comparison verifies logo placement, real-text wordmark, hero typography, yellow emphasis, call-to-action treatment, product screenshot crop, workflow labels, active state, icon family, borders, and spacing. These details remain legible in the focused crop, so no additional region capture was required.

## Comparison History

### Iteration 1

- Earlier P2: the product screenshot reused the old cached asset path and visibly retained the temporary square S mark and prior test identity.
- Fix: captured a fresh empty workspace with the new salamander mark and moved it to `salamandra-dashboard-preview.png` so browsers and Railway fetch the corrected asset.
- Earlier P2: hash navigation could overshoot the workflow when smooth scrolling and browser history restoration overlapped.
- Fix: made hash positioning deterministic and used one 84px document scroll offset instead of stacking section offsets.
- Post-fix evidence: the final comparison files above show the corrected brand and empty workspace; browser measurements place `#workflow` at 84px and preserve the same result through back and forward navigation.

### Iteration 2

- Re-captured and compared the final implementation at the normalized desktop state.
- Result: no actionable P0, P1, or P2 findings.

### Iteration 3

- Earlier P2: the yellow salamander body disappeared against the saturated yellow final call-to-action background, leaving only its black fingerprint lines visible.
- Fix: replaced the flat yellow field with the mockup's pale cream-to-white transition, preserving the supplied mark and restoring the complete animal silhouette.
- Post-fix evidence: desktop and 360px browser checks show the full mark, readable copy, no horizontal overflow, and no console warnings or errors.

## Responsive And Interaction Evidence

- 1280px: 1265px content width, no horizontal overflow, desktop navigation visible.
- 768px: 753px content width, no horizontal overflow, mobile navigation available, 713px product preview.
- 360px: 345px content width, no horizontal overflow, mobile navigation available, 321px product preview.
- The 360px layout also covers a stricter reflow condition than a 200% zoom check on a typical desktop viewport.
- Console review: no warnings or errors on the final landing state.
- Browser routes checked: `/`, `/login`, `/register`, `/terms`, `/privacy`, and `/contact`, including direct refresh.

## Follow-Up Polish

- P3: a later dedicated brand exercise could refine the salamander's smallest-size stroke spacing. The current mark remains clear and recognizable at the required sizes.

## Implementation Checklist

- [x] Brand mark integrated in public and authenticated chrome
- [x] Real empty workspace product preview
- [x] Stable Product and How it works anchors
- [x] Functional six-stage workflow with keyboard operation
- [x] Responsive public pages without horizontal overflow
- [x] Separate Terms and Privacy routes
- [x] No inert public controls

final result: passed
