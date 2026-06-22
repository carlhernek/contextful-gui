import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

interface Props {
  children: string;
  className?: string;
}

export function MarkdownContent({ children, className = "" }: Props) {
  return (
    <div className={`cf-markdown min-w-0 ${className}`}>
      <Markdown remarkPlugins={[remarkGfm, remarkBreaks]}>{children}</Markdown>
    </div>
  );
}
