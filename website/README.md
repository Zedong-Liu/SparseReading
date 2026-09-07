# SparseRead project site

Static Astro site. Visible numbers are imported at build time from
`../launch/claims.yaml` (approved rows only), `../launch/cases/flagship.yaml`,
and `../demo/cases/flagship/`.

```bash
cd website
npm install
SPARSEREAD_BASE=/ npm run review   # http://127.0.0.1:4321/ and /review/
npm run build                      # GitHub Pages base /SparseReading/
npm run check
```

GitHub Pages is https://zedong-liu.github.io/SparseReading/ (`base` `/SparseReading/`). Production builds omit the review banner and `/review/`. Local `SPARSEREAD_BASE=/ npm run review` keeps both.
