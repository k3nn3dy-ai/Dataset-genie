import { useState } from 'react'
import { useDeleteSecret, useSecrets, useSetSecret } from '../../lib/queries'
import { Button, Chip, Input, Label } from '../../components'

export function SecretRow({ name, label, hint }: { name: 'openrouter' | 'huggingface'; label: string; hint: string }) {
  const status = useSecrets()
  const setSecret = useSetSecret()
  const del = useDeleteSecret()
  const [value, setValue] = useState('')
  const isSet = !!status.data?.[name]
  return (
    <div className="flex flex-col gap-1.5">
      <Label hint={<Chip tone={isSet ? 'acid' : 'amber'}>{isSet ? (name === 'huggingface' && status.data?.hf_user ? `set · ${status.data.hf_user}` : 'set') : 'not set'}</Chip>}>{label}</Label>
      <div className="flex items-center gap-2">
        <Input type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} placeholder={isSet ? '••••••••••••••••  (replace)' : hint} className="flex-1" />
        <Button size="md" variant="primary" icon="lock" disabled={value.trim().length < 8} loading={setSecret.isPending} onClick={() => setSecret.mutate({ name, value: value.trim() }, { onSuccess: () => setValue('') })}>Set</Button>
        <Button size="md" variant="danger" icon="trash" disabled={!isSet} loading={del.isPending} onClick={() => del.mutate(name)}>Clear</Button>
      </div>
    </div>
  )
}
