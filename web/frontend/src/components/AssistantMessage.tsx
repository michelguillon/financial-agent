import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  text: string;
}

export function AssistantMessage({ text }: Props) {
  return (
    <div className="rounded-md border border-emerald-200 bg-emerald-50/50 px-4 py-3 text-sm">
      <div className="prose prose-sm max-w-none prose-slate prose-p:my-2 prose-headings:my-2 prose-ul:my-2 prose-pre:bg-white prose-table:text-xs prose-th:bg-slate-100 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-td:border prose-td:border-slate-200 prose-th:border prose-th:border-slate-200">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    </div>
  );
}
