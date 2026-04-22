interface SelectProps {
  id: string;
  label?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  hint?: string;
  disabled?: boolean;
}

export function Select({
  id,
  label,
  value,
  onChange,
  options,
  hint = '',
  disabled = false,
}: SelectProps) {
  return (
    <div className="form-group">
      {label && <label htmlFor={id}>{label}</label>}
      <select
        id={id}
        className="select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {hint && <p className="hint mt-1">{hint}</p>}
    </div>
  );
}