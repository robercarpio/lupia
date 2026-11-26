// Renderizado Markdown mínimo y seguro (sin HTML crudo): párrafos, negritas,
// cursivas, código, listas, encabezados y tablas sencillas.
import { Fragment } from 'react'

function inline(text, key = 'i') {
  const out = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g
  let last = 0, m, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('**')) out.push(<strong key={`${key}${i++}`}>{t.slice(2, -2)}</strong>)
    else if (t.startsWith('`')) out.push(<code key={`${key}${i++}`}>{t.slice(1, -1)}</code>)
    else out.push(<em key={`${key}${i++}`}>{t.slice(1, -1)}</em>)
    last = m.index + t.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function Markdown({ text }) {
  const lines = text.replace(/\r/g, '').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const l = lines[i]
    if (!l.trim()) { i++; continue }
    if (/^#{1,4}\s/.test(l)) { blocks.push(<h4 key={i}>{inline(l.replace(/^#+\s/, ''))}</h4>); i++; continue }
    if (/^\s*[-*•]\s/.test(l)) {
      const items = []
      while (i < lines.length && /^\s*[-*•]\s/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s/, ''))
      blocks.push(<ul key={i}>{items.map((t, k) => <li key={k}>{inline(t, `u${k}`)}</li>)}</ul>)
      continue
    }
    if (/^\s*\d+[.)]\s/.test(l)) {
      const items = []
      while (i < lines.length && /^\s*\d+[.)]\s/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s/, ''))
      blocks.push(<ol key={i}>{items.map((t, k) => <li key={k}>{inline(t, `o${k}`)}</li>)}</ol>)
      continue
    }
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++])
      const cells = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r)).map((r) => r.trim().slice(1, -1).split('|').map((c) => c.trim()))
      blocks.push(
        <div className="md-table" key={i}><table><thead><tr>{cells[0].map((c, k) => <th key={k}>{inline(c)}</th>)}</tr></thead>
          <tbody>{cells.slice(1).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody></table></div>
      )
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*•]\s|\s*\d+[.)]\s|\s*\|)/.test(lines[i])) para.push(lines[i++])
    blocks.push(<p key={i}>{para.map((p, k) => <Fragment key={k}>{k > 0 && <br />}{inline(p, `p${k}`)}</Fragment>)}</p>)
  }
  return <div className="md">{blocks}</div>
}
