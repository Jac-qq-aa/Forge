interface InputProps {
  id: string;
  label?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  type?: 'text' | 'number';
  disabled?: boolean;
}

export function Input({
  id,
  label,
  value,
  onChange,
  placeholder = '',
  hint = '',
  type = 'text',
  disabled = false,
}: InputProps) {
  return (
    <div className="form-group">
      {label && <label htmlFor={id}>{label}</label>}
      <input
        id={id}
        type={type}
        className="input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
      {hint && <p className="hint mt-1">{hint}</p>}
    </div>
  );
}