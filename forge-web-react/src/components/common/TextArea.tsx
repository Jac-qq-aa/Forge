interface TextAreaProps {
  id: string;
  label?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  rows?: number;
  disabled?: boolean;
}

export function TextArea({
  id,
  label,
  value,
  onChange,
  placeholder = '',
  hint = '',
  rows = 4,
  disabled = false,
}: TextAreaProps) {
  return (
    <div className="form-group">
      {label && <label htmlFor={id}>{label}</label>}
      <textarea
        id={id}
        className="textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
      />
      {hint && <p className="hint mt-1">{hint}</p>}
    </div>
  );
}