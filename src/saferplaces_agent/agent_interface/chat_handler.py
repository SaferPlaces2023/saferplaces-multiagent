from __future__ import annotations
import ast
import uuid
import json
import html
from datetime import datetime
from typing import List, Dict, Any, Optional

from langgraph.types import Command, Interrupt
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage, AnyMessage

from IPython.display import display, Markdown, clear_output

from ..common import s3_utils, utils
from .graph_interface import GraphInterface

from . import __GRAPH_REGISTRY__

class ChatMarkdownHandler:
    
    def __init__(self, graph_interface: GraphInterface = None, thread_id: str = None, user_id: str = None, **gi_kwargs):
        """
        Initialize the ChatMarkdownHandler with a GraphInterface instance.
        """
        if graph_interface is None:
            if thread_id is None or user_id is None:
                thread_id = thread_id or str(uuid.uuid4())
                user_id = user_id or "default_user"
                gi_kwargs['project_id'] = gi_kwargs.get('project_id', f"project-{str(uuid.uuid4())}")
                self.graph_interface = __GRAPH_REGISTRY__.register(
                    thread_id=thread_id,
                    user_id=user_id,
                    **gi_kwargs
                )
                print(f"No GraphInterface provided, created a new default one with thread_id={thread_id}, user_id={user_id}, project_id={gi_kwargs['project_id']}")
            else:
                self.graph_interface = __GRAPH_REGISTRY__.register(thread_id=thread_id, user_id=user_id, **gi_kwargs)
        else:
            self.graph_interface = graph_interface
        
        
    def chat_to_markdown(
        self,
        chat: List[AnyMessage | Interrupt] | None = None,
        path: str | None = None,
        title: str | None = None,
        subtitle: Optional[str] = None,
        include_toc: bool = True,
        include_header: bool = True
    ) -> str:
        """
        Genera un file Markdown 'bello' a partire da una lista di messaggi (chat dict).
        - chat: lista di messaggi tipo quelli dell'esempio
        - path: percorso del file .md da creare
        - title/subtitle: titolo opzionale
        - include_toc: inserisce una piccola TOC con ancore ai messaggi
        Ritorna la stringa Markdown generata.
        """
        
        if chat is None:
            chat = self.events
        chat = self.graph_interface.conversation_handler.chat2json(chat)
        if not chat:
            return None
        
        if title is None:
            title = self.graph_interface.conversation_handler.title if self.graph_interface.conversation_handler.title else f"Chat Markdown {datetime.now().isoformat()}"
                    
        
        ROLE_META = {
            "user":      {"emoji": "👤", "label": "User", "color": "#1f6feb"},
            "ai":        {"emoji": "🤖", "label": "Assistant", "color": "#8250df"},
            "tool":      {"emoji": "🛠️", "label": "Tool", "color": "#3fb950"},
            "interrupt": {"emoji": "⏸️", "label": "Interrupt", "color": "#d29922"},
            # fallback handled in code
        }

        def _fence(text: str, lang: str = "") -> str:
            """
            Ritorna un fenced code block che non collida con eventuali triple backtick nel testo.
            Se nel testo compaiono ``` usa ```` come recinzione.
            """
            fence = "```"
            if "```" in text:
                fence = "````"
            lang_tag = lang if lang else ""
            return f"{fence}{lang_tag}\n{text}\n{fence}"

        def _pretty(obj: Any) -> str:
            """Pretty JSON (anche da dict Python), con fallback su str()."""
            try:
                return json.dumps(obj, ensure_ascii=False, indent=2)
            except Exception:
                return str(obj)

        def _maybe_parse_python_like_dict(s: str) -> Any:
            """
            Alcuni tool restituiscono stringhe con apici singoli (non JSON valido).
            Provo a fare ast.literal_eval e poi convertirlo a JSON-friendly.
            """
            try:
                val = ast.literal_eval(s)
                return val
            except Exception:
                # Prova JSON diretto, altrimenti torna la stringa originale
                try:
                    return json.loads(s)
                except Exception:
                    return s
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines: List[str] = []


        if include_header:
            # Front matter leggero (opzionale)
            lines.append(f"---")
            lines.append(f'title: "{title}"')
            if subtitle:
                lines.append(f'subtitle: "{subtitle}"')
            lines.append(f"generated: {now}")
            lines.append(f"---\n")

            # Header
            lines.append(f"# {title}")
            if subtitle:
                lines.append(f"_{subtitle}_")
            lines.append(f"*Generato il {now}*")
            lines.append("")
            lines.append("**Legenda**: 👤 User · 🤖 Assistant · 🛠️ Tool · ⏸️ Interrupt")
            lines.append("")

            # TOC
            if include_toc:
                lines.append("## Indice")
                for i, msg in enumerate(chat, 1):
                    role = msg.get("role", "unknown")
                    meta = ROLE_META.get(role, {"emoji": "📦", "label": role.capitalize()})
                    snippet = (msg.get("content") or "").strip().split("\n")[0]
                    snippet = snippet if snippet else "(vuoto)"
                    # Limita snippet
                    if len(snippet) > 80:
                        snippet = snippet[:77] + "…"
                    lines.append(f"- [{i:02d} · {meta['emoji']} {meta['label']} – {snippet}](#msg-{i:02d})")
                lines.append("")

        # Messaggi
        for i, msg in enumerate(chat, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            meta = ROLE_META.get(role, {"emoji": "📦", "label": role.capitalize(), "color": "#6e7781"})
            emoji, label = meta["emoji"], meta["label"]

            lines.append(f"---")
            lines.append(f"### {i:02d} · {emoji} {label}")
            lines.append(f"<a id='msg-{i:02d}'></a>")

            # Badge ruoli & extra info
            extras = []
            if role == "interrupt":
                itype = msg.get("interrupt_type") or msg.get("interrupt", {}).get("type")
                if itype:
                    extras.append(f"**Tipo:** `{itype}`")
            if role == "user" and msg.get("resume_interrupt"):
                extras.append("**Ripresa interrupt:**")
                extras.append(_fence(_pretty(msg["resume_interrupt"]), "json"))

            if extras:
                lines.append("")
                lines.extend(extras)

            # Corpo messaggio
            if content.strip():
                # Se sembra codice Python (inizia con "The generated code is as follows:" o contiene ```python)
                if "```" in content:
                    # già formattato: lo includo così com'è
                    lines.append("")
                    lines.append(content)
                else:
                    # messaggio normale: uso blockquote per user/ai, code fence per tool se è strutturato
                    if role in ("user", "ai"):
                        lines.append("")
                        for ln in content.splitlines():
                            lines.append(f"> {ln}" if ln.strip() else ">")
                    elif role in ("tool", "interrupt"):
                        # spesso i tool mandano json/stringhe strutturate
                        parsed = _maybe_parse_python_like_dict(content)
                        if isinstance(parsed, (dict, list)):
                            lines.append("")
                            lines.append(_fence(_pretty(parsed), "json"))
                        else:
                            lines.append("")
                            lines.append(_fence(str(parsed), ""))
                    else:
                        lines.append("")
                        lines.append(content)
            else:
                # nessun contenuto, ma potrebbero esserci tool_calls
                if role in ("ai", "tool", "interrupt"):
                    pass  # gestito sotto se ci sono tool_calls
                else:
                    lines.append("\n_(nessun contenuto)_")

            # tool_calls (tipicamente dentro messaggi 'ai')
            tcs = msg.get("tool_calls") or []
            if tcs:
                lines.append("")
                lines.append("<details>")
                lines.append("<summary><strong>Tool calls</strong></summary>\n")
                for j, tc in enumerate(tcs, 1):
                    name = tc.get("name") or tc.get("tool")
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    args = tc.get("args") or {}
                    tc_type = tc.get("type") or ""
                    lines.append(f"**{j}. {name}**  ")
                    if tc_type:
                        lines.append(f"- Tipo: `{tc_type}`")
                    if tc_id:
                        lines.append(f"- ID: `{tc_id}`")
                    if args:
                        lines.append("- Args:")
                        lines.append(_fence(_pretty(args), "json"))
                    # altri campi grezzi, se presenti
                    extra_keys = {k: v for k, v in tc.items() if k not in {"name","args","id","type"}}
                    if extra_keys:
                        lines.append("- Extra:")
                        lines.append(_fence(_pretty(extra_keys), "json"))
                    lines.append("")
                lines.append("</details>")

            lines.append("")

        markdown = "\n".join(lines).rstrip() + "\n"

        # Scrivi su file
        if path is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(markdown)

        return markdown
    

    def layers_to_markdown(self, layers: List[Dict[str, Any]]) -> str:
        """
        Prende in input una lista di dict (layer geospaziali) e restituisce
        una stringa Markdown compatta, con metadati espandibili via <details>.
        Pensata per l'uso con: display(Markdown(layers_to_markdown(layers))).
        """
        def esc(x: Any) -> str:
            # Escape per contenuto HTML/Markdown incluso in tag HTML
            return html.escape(str(x), quote=True)

        lines = []
        for i, layer in enumerate(layers, 1):
            title = esc(layer.get("title", ""))
            desc = esc(layer.get("description", ""))
            src = esc(layer.get("src", ""))
            ltype = esc(layer.get("type", ""))

            lines.append(f"**{title}** — _{desc}_ — `{ltype}`")
            if src:
                url_link = utils.s3uri_to_https(src) if src.startswith("s3://") else src
                lines.append(f"- src: [{src}]({url_link})")
                
            md = layer.get("metadata", {})
            if isinstance(md, dict) and md:
                items = "".join(f"<li><code>{esc(k)}</code>: <code>{esc(v)}</code></li>" for k, v in md.items())
                lines.append(
                    "- <details><summary>metadata</summary>"
                    f"<ul>{items}</ul>"
                    "</details>"
                )
            lines.append("")

        return "\n".join(lines)
    
            
    class ChatMarkdownBreak(StopIteration):
        """Custom exception to stop the chat markdown generator."""
        def __init__(self, message="Chat markdown generation stopped."):
            super().__init__(message)
            
    class ChatMarkdownContinue(Exception):
        """Custom exception to continue the chat markdown generator."""
        def __init__(self, message="Chat markdown generation continued."):
            super().__init__(message)
            
    class ChatMarkdownCommand():
        NEW_CHAT = 'new-chat'
        RESET = 'reset'
        LAYERS = 'layers'
        ADD_LAYER = 'add-layer'
        CLEAR = 'clear'
        HISTORY = 'history'
        MAP = 'map'
        EXPORT = 'export'
        EXIT = 'exit'
        QUIT = 'quit'
        HELP = 'help'
        
    def handle_command(self, command: str):
        """
        Handle chat markdown commands based on the command string.
        Args:
            command (str): The command string to handle.
            self.graph_interface_istance (GraphInterface): The GraphInterface instance to use for handling commands.
        """
        
        def command_new_chat():
            clear_output()
            new_thread_id = str(uuid.uuid4())
            print(f"Starting a new chat with thread_id={new_thread_id}.")
            self.graph_interface = __GRAPH_REGISTRY__.register(
                thread_id=new_thread_id,
                user_id=self.graph_interface.user_id,
                project_id=self.graph_interface.project_id,
                map_handler=self.graph_interface.map_handler
            )
            
        def command_reset():
            print("--- TO BE IMPLEMENTED ---")
        
        def command_layers():
            layers = self.graph_interface.get_state('layer_registry')
            if len(layers) > 0:
                display(Markdown(self.layers_to_markdown(layers)))
            else:
                print("No layers available in the layer registry.")
                
        def command_add_layer():
            src = input("Enter the source URL for the layer (e.g., s3://bucket/path/to/layer.geojson): ")
            title = input("Enter the title for the layer (just a label): ")
            description = input("Enter a description for the layer (optional but recommended): ")
            inferred_layer_type = 'raster' if src.endswith(('.tif', '.tiff')) else 'vector'
            layer_type = input(f"Enter the layer type (default is '{inferred_layer_type}'): ") or inferred_layer_type
            
            if layer_type=='raster':
                metadata_nodata = input("Enter the nodata value for the raster layer (optional, default is nan): ")
                try:
                    metadata_nodata = float(metadata_nodata) if metadata_nodata else 'nan'
                except ValueError:
                    metadata_nodata = 'nan'
                metadata_colormap_name = input("Enter the colormap name for the raster layer (optional, default is 'viridis'): ") or 'viridis'
                metadata = {
                    "nodata": metadata_nodata,
                    "colormap_name": metadata_colormap_name
                }
            elif layer_type=='vector':
                metadata = dict()
            
            self.graph_interface.register_layer(
                src=src,
                title=title,
                description=description,
                layer_type=layer_type,
                metadata=metadata
            )
            
        def command_clear():
            clear_output()
            
        def command_history():
            chat_events = self.graph_interface.conversation_events
            if len(chat_events) > 0:
                clear_output()
                display(Markdown(self.chat_to_markdown(chat=chat_events, include_header=False)))
            else:
                print("No past messages in the conversation.")
            
        def command_map():
            if self.graph_interface.map_handler:
                display(self.graph_interface.map_handler.m)
                raise self.ChatMarkdownBreak("Map displayed.")
            else:
                print("No map handler available.")
                
        def command_export():
            export_path = input(f"Enter the filename to save the chat markdown file (default is chat_{self.graph_interface.thread_id}.md'): ")
            export_path = export_path or f"chat_{self.graph_interface.thread_id}.md"
            title = input("Enter the title for the chat (optional): ") or None
            self.chat_to_markdown(
                chat=self.graph_interface.conversation_events,
                path=export_path,
                title=title,
                subtitle="Exported conversation",
                include_toc=True,
                include_header=True
            )
            # FIXEM: export_uri = f"{s3_utils._STATE_BUCKET_(self.graph_interface.graph_state)}/conversations/{self.graph_interface.thread_id}/{export_path}"
            export_uri = f"{s3_utils._BASE_BUCKET}/conversations/{self.graph_interface.thread_id}/{export_path}"
            s3_utils.s3_upload(filename=export_path, uri=export_uri, remove_src=True)
            export_url = html.escape(utils.s3uri_to_https(export_uri), quote=True)
            display(Markdown(f"Chat exported to: [{export_uri}]({export_url})"))
                
        def command_exit():
            print("Exiting the conversation.")
            raise self.ChatMarkdownBreak("Exiting the conversation.")
        
        def command_help():
            commands = [cmd for cmd in dir(self.ChatMarkdownCommand) if not cmd.startswith('_')]
            help_text = "\n".join([f"/{cmd}: {cmd.replace('_', ' ').capitalize()}" for cmd in commands])
            print(f"Available commands:\n{help_text}")
        
        if command == self.ChatMarkdownCommand.NEW_CHAT:
            command_new_chat()
        elif command == self.ChatMarkdownCommand.RESET:
            command_reset()
        elif command == self.ChatMarkdownCommand.LAYERS:
            command_layers()
        elif command == self.ChatMarkdownCommand.ADD_LAYER:
            command_add_layer()
        elif command == self.ChatMarkdownCommand.CLEAR:
            command_clear()
        elif command == self.ChatMarkdownCommand.HISTORY:
            command_history()
        elif command == self.ChatMarkdownCommand.MAP:
            command_map()
        elif command == self.ChatMarkdownCommand.EXPORT:
            command_export()
        elif command == self.ChatMarkdownCommand.EXIT or command == self.ChatMarkdownCommand.QUIT:
            command_exit()
        elif command == self.ChatMarkdownCommand.HELP:
            command_help()
        else:
            print(f"Unknown command: {command}")
        
        raise self.ChatMarkdownContinue("Continuing the conversation after command.")
    
    
    def run(self):
        def markdown_interaction(user_prompt: str, display_output: bool = True):
            gen = (
                Markdown(self.chat_to_markdown(chat=e, include_header=False))
                for e in self.graph_interface.user_prompt(prompt=user_prompt, state_updates={'avaliable_tools': []})
            )
            if display_output:
                for md in gen:
                    display(md)
            else:
                yield from gen
        
        prompt_input = lambda: input("Enter your prompt (type 'exit' to quit conversation): ")

        while True:
            p = prompt_input()
            if p.lower() == 'exit':
                break
            
            if p.startswith('/'):
                command = p[1:].strip()
                try:
                    self.handle_command(command)
                except self.ChatMarkdownBreak:
                    break
                except self.ChatMarkdownContinue:
                    continue
                    
            for md in markdown_interaction(p, display_output=True):
                continue