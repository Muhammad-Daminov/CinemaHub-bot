/** Paginated user list with balance and premium status. */
import { useCallback, useEffect, useState } from "react";
import { adminApi } from "../lib/api";
import type { AdminUser } from "../types/admin";
import { Badge, Button, EmptyState, SectionTitle, TextInput, formatMoney } from "./ui";

const PAGE_SIZE = 20;

export function UsersPanel() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await adminApi.listUsers({
        q: query || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      setUsers(result.items);
      setTotal(result.total);
    } catch {
      setUsers([]);
      setTotal(0);
    }
  }, [query, page]);

  useEffect(() => {
    load();
  }, [load]);

  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="space-y-3">
      <SectionTitle>Foydalanuvchilar</SectionTitle>

      <TextInput
        value={query}
        onChange={(value) => {
          setPage(0);
          setQuery(value);
        }}
        placeholder="Username bo'yicha qidirish…"
      />

      <p className="font-mono text-[11px] text-ink-dim">{total} ta foydalanuvchi</p>

      {users.length === 0 ? (
        <EmptyState message="Foydalanuvchi topilmadi." />
      ) : (
        <ul className="space-y-2">
          {users.map((user) => (
            <li key={user.id} className="rounded-xl border border-surface-hi bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-body text-sm text-ink">
                    {user.username ? `@${user.username}` : (user.full_name ?? "—")}
                  </p>
                  <p className="font-mono text-[11px] text-ink-dim">{user.telegram_id}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {user.is_banned && <Badge>Bloklangan</Badge>}
                  {user.is_premium && <Badge active>Premium</Badge>}
                  <span className="font-mono text-sm text-marquee">{formatMoney(user.balance)}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {lastPage > 0 && (
        <div className="flex items-center justify-between pt-2">
          <Button tone="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Oldingi
          </Button>
          <span className="font-mono text-xs text-ink-dim">
            {page + 1} / {lastPage + 1}
          </span>
          <Button tone="ghost" disabled={page >= lastPage} onClick={() => setPage((p) => p + 1)}>
            Keyingi
          </Button>
        </div>
      )}
    </div>
  );
}
