import type { Choice } from "@/app/lib/api";

export function ChoiceButtons({
  choices,
  onSelect,
  disabled,
}: {
  choices: Choice[];
  onSelect: (choiceId: string) => void;
  disabled?: boolean;
}) {
  if (choices.length === 0) return null;

  return (
    <div className="flex flex-wrap justify-start gap-2 pl-1">
      {choices.map((choice) => (
        <button
          key={choice.choice_id}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(choice.choice_id)}
          className="rounded-full border border-accent bg-accent-soft px-4 py-2 text-sm font-medium text-accent-hover transition hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
        >
          {choice.label}
        </button>
      ))}
    </div>
  );
}
