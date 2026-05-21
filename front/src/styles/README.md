# Design tokens — Holter Dashboard

Tokens centralizados en `tokens.css`. Definidos con la directiva `@theme` de Tailwind v4 (CSS-first config); cada token genera utilities consumibles desde clases.

La grilla replica `ican-web/src/common/theme.ts` con dos diferencias:

- **Primary**: navy custom (de `#0b2185` a `#e5e7fe`/`#010425`), no el morado de ican.
- **Tipografía**: se suma Inter (ican usaba la sans-serif default del browser).

## Cómo se usan

Las utilities resultantes siguen la convención de Tailwind:

```tsx
<div className="bg-primary-50 text-primary-500 border border-gray-100 rounded-lg shadow-card">
  <h1 className="text-h4">Pacientes</h1>
  <p className="text-body2 text-gray-600">Listado</p>
</div>
```

## Tabla de tokens

### Colores (utilities `bg-*`, `text-*`, `border-*`, etc.)

| Categoría     | Tokens                                                                    | Notas                       |
| ------------- | ------------------------------------------------------------------------- | --------------------------- |
| Primary       | `primary-50`..`primary-950`                                               | Navy. Brand: `primary-500`. |
| Gray          | `gray-50`..`gray-900`                                                     | Copia de ican.              |
| Black / White | `black`, `white`                                                          | Estáticos.                  |
| Error         | `error-50`, `error-100`, `error-300`, `error-400`, `error-700`            | Copia de ican.              |
| Success       | `success-100`, `success-200`, `success-500`, `success-700`                | Copia de ican.              |
| Warning       | `warning-100`, `warning-300`, `warning-400`, `warning-500`, `warning-700` | Copia de ican.              |
| Info          | `info-100`, `info-300`, `info-400`, `info-500`, `info-700`                | Copia de ican.              |

### Semánticos (cambian con `data-theme`)

| Token           | Light    | Dark     |
| --------------- | -------- | -------- |
| `bg`            | white    | gray-900 |
| `bg-muted`      | gray-50  | gray-800 |
| `fg`            | gray-900 | white    |
| `fg-muted`      | gray-600 | gray-300 |
| `border`        | gray-100 | gray-700 |
| `border-strong` | gray-200 | gray-600 |

Uso: `<body class="bg-bg text-fg">`. Para variantes dark explícitas en clases puntuales, usar el variant `dark:` (definido vía `@custom-variant dark`).

### Tipografía (utilities `text-*`)

Replica de `typographyVariant` de ican.

| Token         | Tamaño | Peso |
| ------------- | ------ | ---- |
| `text-h1`     | 56px   | 400  |
| `text-h2`     | 38px   | 400  |
| `text-h3`     | 32px   | 400  |
| `text-h4`     | 28px   | 400  |
| `text-h5`     | 24px   | 500  |
| `text-h6`     | 20px   | 500  |
| `text-body1`  | 16px   | 400  |
| `text-body2`  | 14px   | 400  |
| `text-body3`  | 12px   | 400  |
| `text-body4`  | 10px   | 400  |
| `text-body5`  | 8px    | 400  |
| `text-helper` | 12px   | 400  |

Fuente default: Inter (`font-sans`). Mono: stack del sistema (`font-mono`).

### Radios (utilities `rounded-*`)

| Token          | Valor  | Cuándo usar             |
| -------------- | ------ | ----------------------- |
| `rounded-sm`   | 6px    | Inputs pequeños         |
| `rounded-md`   | 8px    | Inputs default          |
| `rounded-lg`   | 12px   | Botones (estándar ican) |
| `rounded-xl`   | 15px   | Cards (estándar ican)   |
| `rounded-2xl`  | 20px   | Modales                 |
| `rounded-full` | 9999px | Avatares, pills         |

### Sombras (utilities `shadow-*`)

| Token         | Valor                              | Notas                          |
| ------------- | ---------------------------------- | ------------------------------ |
| `shadow-sm`   | sombra fina                        | Inputs focus, hover livianos   |
| `shadow-md`   | media                              | Dropdowns, popovers            |
| `shadow-lg`   | grande                             | Modales                        |
| `shadow-card` | `0 4px 24px rgba(214,203,252,0.3)` | Replica exacta de ican (cards) |

### Spacing custom

| Token                   | Valor | Notas                          |
| ----------------------- | ----- | ------------------------------ |
| `sidebar` (`w-sidebar`) | 70px  | Ancho del sidebar (ican style) |
| `topbar` (`h-topbar`)   | 64px  | Altura del topbar              |

## Modo dark

Tokens semánticos (`bg`, `fg`, `border`, etc.) se sobrescriben bajo `[data-theme='dark']`. Para activarlo:

```html
<html data-theme="dark"></html>
```

Toggle UI: pendiente (otro ticket). Por ahora se valida manualmente desde DevTools.

## Mapeo con ican-web

| ican `theme.ts`                     | aquí                                             |
| ----------------------------------- | ------------------------------------------------ |
| `primary100..primary900` (morado)   | `primary-50..primary-950` (navy, valores nuevos) |
| `gray50..gray900`                   | `gray-50..gray-900` (mismos hex)                 |
| `error50/100/300/400/700`           | `error-50/100/300/400/700` (mismos hex)          |
| `warning100/300/400/500/700`        | idem                                             |
| `success100/200/500/700`            | idem                                             |
| `info100/300/400/500/700`           | idem                                             |
| `oncoRed`, `oncoPurple`, etc.       | NO migrados (vocabulario de oncología)           |
| `typographyVariant.h1..h6/body1..5` | `text-h1..h6/body1..5`                           |
| Sombra de cards                     | `shadow-card` (mismo rgba)                       |

## Cosas que NO entran (en este ticket)

- Toggle dark/light en UI.
- Componentes base (Button, Input, Card) — TES-12.
- Iconografía custom (se usa `lucide-react`).
