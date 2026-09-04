import Markdown from "react-markdown";

interface MarkdownTextProps {
  text: string;
  className?: string;
}

/** 助手回复渲染层：react-markdown 包装 + 项目内 md-body 样式。 */
export function MarkdownText({ text, className = "" }: MarkdownTextProps) {
  return (
    <div className={`md-body break-words ${className}`.trim()}>
      <Markdown>{text}</Markdown>
    </div>
  );
}
