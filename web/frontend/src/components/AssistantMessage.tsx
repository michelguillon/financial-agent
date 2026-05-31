import ReactMarkdown from 'react-markdown';

interface Props {
  text: string;
}

export function AssistantMessage({ text }: Props) {
  return (
    <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-4 py-3 text-sm">
      <div className="prose prose-sm max-w-none prose-slate prose-p:my-2 prose-headings:my-2 prose-ul:my-2 prose-pre:bg-white">
        <ReactMarkdown>{text}</ReactMarkdown>
      </div>
    </div>
  );
}
