import { useState } from 'react'

// Page numbers to render: always the first and last page, a window around
// the current page, and null where a gap becomes an ellipsis.
function pageItems(page, pages, around = 1) {
  const items = []
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - page) <= around) items.push(p)
    else if (items[items.length - 1] !== null) items.push(null)
  }
  return items
}

const BTN = 'min-w-8 h-8 px-2.5 rounded-md border text-sm'

// jump: also show a "Go to page" box, for lists too long to click through
export default function Pagination({ page, pages, total, pageSize, onChange, jump = false }) {
  const [target, setTarget] = useState('')
  if (total === 0) return null
  const goTo = (e) => {
    e.preventDefault()
    const p = Math.round(Number(target))
    if (p >= 1 && p <= pages) onChange(p)
    setTarget('')
  }
  const first = (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <nav className="flex flex-wrap items-center justify-between gap-3 mt-2" aria-label="Pagination">
      <div className="text-sm text-dark2">
        Showing <b className="text-dark">{first}–{last}</b> of <b className="text-dark">{total}</b>
      </div>
      {pages > 1 && (
        <div className="flex flex-wrap items-center gap-1">
          <button type="button" className={`${BTN} bg-white border-border text-dark2 disabled:opacity-40`}
            disabled={page === 1} onClick={() => onChange(page - 1)}>
            ‹ Prev
          </button>
          {pageItems(page, pages).map((p, i) => (p === null ? (
            <span key={`gap${i}`} className="px-1 text-dark2">…</span>
          ) : (
            <button
              key={p} type="button" aria-current={p === page ? 'page' : undefined} onClick={() => onChange(p)}
              className={`${BTN} ${p === page
                ? 'bg-accentDeep text-white border-accentDeep'
                : 'bg-white text-dark2 border-border'}`}
            >
              {p}
            </button>
          )))}
          <button type="button" className={`${BTN} bg-white border-border text-dark2 disabled:opacity-40`}
            disabled={page === pages} onClick={() => onChange(page + 1)}>
            Next ›
          </button>
          {jump && (
            <form onSubmit={goTo} className="flex items-center gap-1 ml-2 text-sm text-dark2">
              <label htmlFor="page-jump">Go to page</label>
              <input id="page-jump" type="number" min={1} max={pages} value={target} placeholder={String(page)}
                onChange={(e) => setTarget(e.target.value)}
                className="w-16 h-8 px-2 border border-border rounded-md bg-white text-dark" />
              <button type="submit" className={`${BTN} bg-white border-border text-dark2`}>Go</button>
            </form>
          )}
        </div>
      )}
    </nav>
  )
}
