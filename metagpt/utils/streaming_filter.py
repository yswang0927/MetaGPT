from typing import Union, Iterable, Optional, Set

class StreamingRemoveCodeBlockFilter:
    """
    过滤LLM的流式输出chunk内容，将内容中代码块(使用 ```xxx ```包裹的)部分内容去除，
    只流式输出代码块之外的内容，同时要支持代码块嵌套的情况。
    """
    def __init__(self, lang: Union[str, Iterable[str]] = "*"):
        """
        Args:
            lang:
                - "" or "*" or []: Remove all code blocks (any language).
                - "json": Only remove ```json blocks.
                - ["json", "yaml"]: Remove json or yaml blocks.
        """
        self.target_langs: Optional[Set[str]] = None

        if lang == "*" or lang == "" or lang == [] or (isinstance(lang, list) and len(lang) == 0):
            self.target_langs = None  # None implies all languages
        elif isinstance(lang, str):
            self.target_langs = {lang.lower()}
        elif isinstance(lang, Iterable):
            self.target_langs = {l.lower() for l in lang}

        # State definitions
        # OUTSIDE: Normal text
        # OUTSIDE_CHECKING_TICKS: Found backticks, checking if it's a start delimiter
        # CAPTURING_LANG: Found start delimiter, reading language
        # INSIDE: Inside block
        # INSIDE_CHECKING_TICKS: Found backticks inside, checking if it's an end delimiter
        # INSIDE_CHECKING_TRAIL: Found valid closing ticks, checking for trailing spaces/newline
        self.state = "OUTSIDE"
        self.buffer = ""
        self.active_delimiter_len = 0
        self.should_remove = False

    def _check_lang(self, lang: str) -> bool:
        if self.target_langs is None:
            return True
        return lang.lower() in self.target_langs

    def filter_chunk(self, chunk: str) -> str:
        output = []

        for char in chunk:
            processed = False
            while not processed:
                if self.state == "OUTSIDE":
                    if char == '`':
                        self.state = "OUTSIDE_CHECKING_TICKS"
                        self.buffer = "`"
                        processed = True
                    else:
                        output.append(char)
                        processed = True

                elif self.state == "OUTSIDE_CHECKING_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        if len(self.buffer) >= 3:
                            self.active_delimiter_len = len(self.buffer)
                            self.buffer = ""
                            self.state = "CAPTURING_LANG"
                            # Re-evaluate char
                        else:
                            output.append(self.buffer)
                            output.append(char)
                            self.buffer = ""
                            self.state = "OUTSIDE"
                            processed = True

                elif self.state == "CAPTURING_LANG":
                    if char == '\n':
                        lang = self.buffer.strip()
                        self.should_remove = self._check_lang(lang)

                        if not self.should_remove:
                            output.append("`" * self.active_delimiter_len)
                            output.append(self.buffer)
                            output.append(char)

                        self.buffer = ""
                        self.state = "INSIDE"
                        processed = True
                    else:
                        self.buffer += char
                        processed = True

                elif self.state == "INSIDE":
                    if char == '`':
                        self.state = "INSIDE_CHECKING_TICKS"
                        self.buffer = "`"
                        processed = True
                    else:
                        if not self.should_remove:
                            output.append(char)
                        processed = True

                elif self.state == "INSIDE_CHECKING_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        # Check if we have enough ticks for a closer
                        if len(self.buffer) >= self.active_delimiter_len:
                            # Potential closer. Check trailing char.
                            if char == '\n':
                                # Confirmed closer
                                if self.should_remove:
                                    # Swallow buffer (ticks) and char (newline)
                                    # Wait, usually the newline after code block IS part of the block structure
                                    # but often we want to keep the newline to separate subsequent text?
                                    # Markdown spec: the newline ending the fence is part of the fence.
                                    # So if we remove the block, we remove the newline too.
                                    pass
                                else:
                                    output.append(self.buffer)
                                    output.append(char)

                                self.state = "OUTSIDE"
                                self.active_delimiter_len = 0
                                self.should_remove = False
                                self.buffer = ""
                                processed = True
                            elif char == ' ' or char == '\t':
                                # Allow trailing spaces
                                self.buffer += char
                                self.state = "INSIDE_CHECKING_TRAIL"
                                processed = True
                            else:
                                # Invalid trailer (e.g. " or m), so it's NOT a closer
                                if not self.should_remove:
                                    output.append(self.buffer)
                                    output.append(char)
                                self.buffer = ""
                                self.state = "INSIDE"
                                processed = True
                        else:
                            # Not enough ticks, just content
                            if not self.should_remove:
                                output.append(self.buffer)
                                output.append(char)
                            self.buffer = ""
                            self.state = "INSIDE"
                            processed = True

                elif self.state == "INSIDE_CHECKING_TRAIL":
                    if char == '\n':
                        # Confirmed closer after spaces
                        if self.should_remove:
                            pass
                        else:
                            output.append(self.buffer)
                            output.append(char)

                        self.state = "OUTSIDE"
                        self.active_delimiter_len = 0
                        self.should_remove = False
                        self.buffer = ""
                        processed = True
                    elif char == ' ' or char == '\t':
                        self.buffer += char
                        processed = True
                    else:
                        # Hit non-space non-newline -> Invalid closer
                        if not self.should_remove:
                            output.append(self.buffer)
                            output.append(char)
                        self.buffer = ""
                        self.state = "INSIDE"
                        processed = True

        return "".join(output)