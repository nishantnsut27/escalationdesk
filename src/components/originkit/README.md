# Originkit source

These three files were copied by the official Originkit CLI and are kept unchanged:

- [Vector Wordmark](https://www.originkit.dev/components/vector-wordmark): `ui/vector-wordmark.tsx`
- [Spider Button](https://www.originkit.dev/components/spider-button): `ui/spider-button.tsx`
- [Moonlit Petals](https://www.originkit.dev/components/moonlit-petals): `ui/moonlit-petals.tsx`

TraceDesk configures their exported props in the neighboring app adapters. The
Spider adapter uses the original native button, with a portal for its icon/label
and a bridge for `disabled` and `aria-busy`. Decorative drawing space does not
intercept nearby controls. Motion, visibility and context-loss fallbacks are
managed outside the downloaded files.

The original Moonlit Petals poster is served locally from
`public/originkit/moonlit-petals-poster.jpg` for static/loading presentation. Its
source is the component page's official Originkit CDN poster. No API key or
Originkit service is required at runtime.
