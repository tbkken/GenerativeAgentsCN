"""Bounded author-resource queries; detail content never belongs in list rows."""
from math import ceil

from sqlalchemy import func, or_, select


def page_result(items, total, page, page_size, **extra):
    return dict(items=items, total=total, page=page, page_size=page_size,
                total_pages=max(1, ceil(total / page_size)), **extra)


def bounds(page, page_size):
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("Resource pagination requires page >= 1 and 1 <= page_size <= 100")


def search_predicate(query, *columns):
    # Treat author input literally, including SQL LIKE wildcard characters.
    text = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(*(column.ilike(f"%{text}%", escape="\\") for column in columns))


def query_page(session, model, columns, *, predicates=(), order, page, page_size):
    bounds(page, page_size)
    total = int(session.scalar(select(func.count()).select_from(model).where(*predicates)) or 0)
    rows = session.execute(select(*columns).where(*predicates).order_by(*order)
                           .offset((page - 1) * page_size).limit(page_size)).mappings()
    return [dict(row) for row in rows], total


def dates(item):
    for key in ("created_at", "updated_at", "archived_at"):
        if item.get(key) is not None:
            item[key] = item[key].isoformat()
    return item
