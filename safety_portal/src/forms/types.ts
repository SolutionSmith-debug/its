// TypeScript mirror of safety_portal/forms/meta-schema.json. The SAME JSON
// definitions are validated against that schema by tests/test_form_definitions.py
// and rendered to PDF by the Python renderer — this file is the display runtime's
// view of the one contract.

export type Input =
  | "text" | "textarea" | "date" | "time" | "number" | "select" | "signature";

export interface Field {
  key: string;
  label: string;
  input: Input;
  options?: string[];
  required?: boolean;
}

export interface Item {
  key: string;
  label: string;
  kind?: "rated" | "numeric" | "circle_one" | "text";
  options?: string[];
  scale?: string[];
  comment?: boolean;
}

export interface Group {
  key: string;
  label: string;
  scale: string[];
  comment_per_item?: boolean;
  items: Item[];
}

export interface ContentBlock {
  heading?: string;
  body: string;
}

export type Section =
  | { type: "header"; title?: string; fields: Field[] }
  | { type: "static_text"; text: string; emphasis?: "footer" | "heading" | "legal" }
  | { type: "repeating_table"; key: string; title?: string; columns: Field[]; min_rows?: number; allow_add?: boolean }
  | { type: "signature_table"; key: string; title?: string; columns: Field[]; min_rows?: number; allow_add?: boolean }
  | { type: "checklist"; key: string; title?: string; groups: Group[] }
  | { type: "freeform"; key: string; label: string; input?: "textarea" | "text" }
  | { type: "content_blocks"; key: string; title?: string; source_pdf?: string; blocks: ContentBlock[] };

export interface FormDefinition {
  form_code: string;
  parent_form_code: string;
  form_name: string;
  variant_label: string | null;
  version: number;
  archetype: string;
  source_pdf: string;
  branding?: { logo?: boolean; title?: string };
  sections: Section[];
}
