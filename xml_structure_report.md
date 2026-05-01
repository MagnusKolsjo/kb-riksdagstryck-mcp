# XML-strukturrapport — KB:s riksdagstryck

Analyserade 24 XML-filer.

## Rotnod och namnrymd

- `<document>` — 24 filer

- Namnrymd: `http://www.abbyy.com/FineReader_xml/FineReader10-schema-v1.xml` — 24 filer

## Vanligaste taggar (förekommer i flest filer)

| Tagg | Filer | Snitt per fil |
|---|---|---|
| `<formatting>` | 24 | 16327 |
| `<line>` | 24 | 16323 |
| `<rect>` | 24 | 21041 |
| `<par>` | 24 | 13142 |
| `<block>` | 24 | 3666 |
| `<region>` | 24 | 3666 |
| `<text>` | 24 | 9412 |
| `<separator>` | 24 | 2767 |
| `<start>` | 24 | 2767 |
| `<end>` | 24 | 2767 |
| `<elemId>` | 24 | 620 |
| `<page>` | 24 | 160 |
| `<stream>` | 24 | 313 |
| `<section>` | 24 | 33 |
| `<mainText>` | 24 | 26 |
| `<document>` | 24 | 1 |
| `<documentData>` | 24 | 1 |
| `<sections>` | 24 | 1 |
| `<cell>` | 14 | 14590 |
| `<row>` | 14 | 1153 |

## Taggar med mest textinnehåll (kandidater för chunking)

- `<formatting>` — primär textnod i 24 av 24 filer

## Attribut per tagg

- `<block>`: `@b`, `@blockType`, `@l`, `@pageElemId`, `@r`, `@t`
- `<cell>`: `@bottomBorder`, `@colSpan`, `@height`, `@leftBorder`, `@rightBorder`, `@rowSpan`, `@topBorder`, `@width`
- `<document>`: `@languages`, `@pagesCount`, `@producer`, `@schemaLocation`, `@version`
- `<elemId>`: `@id`
- `<end>`: `@x`, `@y`
- `<formatting>`: `@lang`
- `<line>`: `@b`, `@baseline`, `@l`, `@r`, `@t`
- `<mainText>`: `@columnCount`
- `<page>`: `@height`, `@resolution`, `@width`
- `<par>`: `@align`, `@dropCapCharsCount`, `@hasOverflowedHead`, `@hasOverflowedTail`, `@leftIndent`, `@lineSpacing`, `@rightIndent`, `@startIndent`
- `<rect>`: `@b`, `@l`, `@r`, `@t`
- `<separator>`: `@thickness`, `@type`
- `<start>`: `@x`, `@y`
- `<stream>`: `@beginPage`, `@endPage`, `@role`
- `<text>`: `@id`, `@orientation`

## Exempeltext per tagg

### `<formatting>`
```
RidLttskaptt-ch Wclil
```

## Träd-exempel (en fil per stånd)

### roa_1789_2_.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage @endPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@align @leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
      <row>
        <cell>  [@leftBorder @topBorder @rightBorder @bottomBorder @width @height]
          <text>  [@id]
            <par>  [@leftIndent]
```

### rdbesl_1766__.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
```

### bn_1731-1734___03.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage @endPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
      <row>
        <cell>  [@leftBorder @topBorder @rightBorder @bottomBorder @width @height]
          <text>  [@id]
            <par>  [@leftIndent]
```

### pr_1834-35_8__03.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage @endPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
```

### bg_1840-41_4__02.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage @endPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
```

### sakreg_1809-1866_1__020001.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <row>
        <cell>  [@leftBorder @bottomBorder @width @height]
          <text>  [@id]
            <par>  [@leftIndent @lineSpacing]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
```

### persreg_1809-1866___02.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage]
          <elemId>  [@id]
          <mainText>  [@columnCount]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
      <row>
        <cell>  [@colSpan @topBorder @rightBorder @bottomBorder @width @height]
          <text>  [@id]
            <par>  [@leftIndent]
```

### ku_1810__.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @pageElemId @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@align @leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
      <row>
        <cell>  [@leftBorder @topBorder @rightBorder @bottomBorder @width @height]
          <text>  [@id]
            <par>  [@lineSpacing]
```

### bih_1823_3_reg__02.xml
```
<document>  [@version @producer @pagesCount @languages @schemaLocation]
  <documentData>
    <sections>
      <section>
        <stream>  [@role @beginPage @endPage]
          <mainText>  [@columnCount]
          <elemId>  [@id]
  <page>  [@width @height @resolution]
    <block>  [@blockType @l @t @r @b]
      <region>
        <rect>  [@l @t @r @b]
      <text>  [@id]
        <par>  [@leftIndent]
          <line>  [@baseline @l @t @r @b]
            <formatting>  [@lang]
      <separator>  [@type @thickness]
        <start>  [@x @y]
        <end>  [@x @y]
```

## Parsfel

Inga parsfel.
