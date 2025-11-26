from typing import Union, Iterable, Optional, Set


class StreamingRemoveCodeBlockFilter:
    def __init__(self, lang: Union[str, Iterable[str]] = "*"):
        self.target_langs: Optional[Set[str]] = None
        if lang == "*" or lang == "" or lang == [] or (isinstance(lang, list) and len(lang) == 0):
            self.target_langs = None
        elif isinstance(lang, str):
            self.target_langs = {lang.lower()}
        elif isinstance(lang, Iterable):
            self.target_langs = {l.lower() for l in lang}

        # 状态定义保持不变
        self.state = "OUTSIDE"
        self.buffer = ""
        self.active_delimiter_len = 0
        self.should_remove = False

        # [新增] 追踪是否处于行首，用于判断闭合标记是否合法
        # 初始默认为 True，因为第一行肯定是行首
        self.is_start_of_line = True

    def _check_lang(self, lang: str) -> bool:
        if self.target_langs is None:
            return True
        return lang.lower() in self.target_langs

    def filter_chunk(self, chunk: str) -> str:
        output = []

        for char in chunk:
            processed = False
            while not processed:
                # --- 1. 普通文本状态 ---
                if self.state == "OUTSIDE":
                    if char == '`':
                        self.state = "OUTSIDE_CHECKING_TICKS"
                        self.buffer = "`"
                        processed = True
                    else:
                        output.append(char)
                        self.is_start_of_line = (char == '\n')
                        processed = True

                # --- 2. 检查开始标记 ---
                elif self.state == "OUTSIDE_CHECKING_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        # 只有 >= 3 个反引号才算代码块开始，且通常要求在行首（虽然Markdown对此宽容，但作为过滤器严谨点好）
                        # 这里简化逻辑：只要凑够3个就检查语言
                        if len(self.buffer) >= 3:
                            self.active_delimiter_len = len(self.buffer)
                            self.buffer = ""
                            self.state = "CAPTURING_LANG"
                            # Don't mark processed, re-eval char in new state
                        else:
                            # 不是代码块，是普通的反引号
                            output.append(self.buffer)
                            self.buffer = ""
                            self.state = "OUTSIDE"
                            # Don't mark processed, re-eval char in OUTSIDE

                # --- 3. 获取语言类型 ---
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
                        # 进入代码块内部，新的一行开始，置为 True
                        self.is_start_of_line = True
                        processed = True
                    else:
                        self.buffer += char
                        processed = True

                # --- 4. 代码块内部 ---
                elif self.state == "INSIDE":
                    # [关键修复] 只有在行首遇到的 ` 才可能是闭合标记
                    if char == '`' and self.is_start_of_line:
                        self.state = "INSIDE_CHECKING_TICKS"
                        self.buffer = "`"
                        processed = True
                    else:
                        if not self.should_remove:
                            output.append(char)
                        # 更新行首状态
                        self.is_start_of_line = (char == '\n')
                        processed = True

                # --- 5. 检查闭合标记 ---
                elif self.state == "INSIDE_CHECKING_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        # 检查是否匹配开始时的长度
                        if len(self.buffer) >= self.active_delimiter_len:
                            if char == '\n':
                                # 完美闭合
                                if not self.should_remove:
                                    output.append(self.buffer)
                                    output.append(char)

                                # 重置状态
                                self.state = "OUTSIDE"
                                self.active_delimiter_len = 0
                                self.should_remove = False
                                self.buffer = ""
                                self.is_start_of_line = True
                                processed = True
                            elif char in (' ', '\t'):
                                # 允许尾随空格
                                self.buffer += char
                                self.state = "INSIDE_CHECKING_TRAIL"
                                processed = True
                            else:
                                # 后面跟了其他字符，说明不是闭合标记（比如只是代码里的一行反引号）
                                # 回退：把buffer里的反引号吐出来（如果不需要移除）
                                if not self.should_remove:
                                    output.append(self.buffer)
                                    output.append(char)  # 当前字符也输出

                                self.buffer = ""
                                self.state = "INSIDE"
                                self.is_start_of_line = False  # 因为当前 char 不是 \n
                                processed = True
                        else:
                            # 长度不够（例如只遇到 2 个反引号）
                            if not self.should_remove:
                                output.append(self.buffer)
                                output.append(char)
                            self.buffer = ""
                            self.state = "INSIDE"
                            self.is_start_of_line = (char == '\n')
                            processed = True

                # --- 6. 检查闭合标记后的尾随空格 ---
                elif self.state == "INSIDE_CHECKING_TRAIL":
                    if char == '\n':
                        # 真正的闭合
                        if not self.should_remove:
                            output.append(self.buffer)
                            output.append(char)

                        self.state = "OUTSIDE"
                        self.active_delimiter_len = 0
                        self.should_remove = False
                        self.buffer = ""
                        self.is_start_of_line = True
                        processed = True
                    elif char in (' ', '\t'):
                        self.buffer += char
                        processed = True
                    else:
                        # 它是空格后跟了非换行符，无效闭合
                        if not self.should_remove:
                            output.append(self.buffer)
                            output.append(char)
                        self.buffer = ""
                        self.state = "INSIDE"
                        self.is_start_of_line = False
                        processed = True

        return "".join(output)

    def flush(self) -> str:
        """
        处理流结束时缓冲区剩余的内容
        """
        output = []

        # 如果还在 OUTSIDE 检查反引号，说明这些反引号是文本结尾，应输出
        if self.state == "OUTSIDE_CHECKING_TICKS":
            output.append(self.buffer)

        # 如果在 CAPTURING_LANG，说明代码块头没写完，视作普通文本输出
        elif self.state == "CAPTURING_LANG":
            # 把之前吞掉的 ``` 补回来
            output.append("`" * self.active_delimiter_len)
            output.append(self.buffer)

        # 如果在 INSIDE，且我们不移除，则无事发生（内容已实时输出）
        # 如果在 INSIDE，且我们正在移除，也无事发生（内容已被丢弃）

        # 如果在 INSIDE_CHECKING_TICKS，说明流以反引号结尾
        # 无论是否 should_remove，因为没有闭合（缺少\n），
        # 严格来说这不算闭合。但如果是移除模式，这部分buffer应该被丢弃还是保留？
        # 通常流断了，我们假设它没闭合，所以把 buffer (反引号) 原样输出或保留。
        # 如果在移除模式下，这些反引号可能是代码的一部分，也可能是未完成的闭合。
        # 安全起见，如果我们在移除模式，且流结束了，我们通常不再输出这些悬挂的反引号。
        elif self.state in ["INSIDE_CHECKING_TICKS", "INSIDE_CHECKING_TRAIL"]:
            if not self.should_remove:
                output.append(self.buffer)

        # 重置状态
        self.buffer = ""
        self.state = "OUTSIDE"
        self.is_start_of_line = True
        self.should_remove = False

        return "".join(output)


from typing import Union, Iterable, List, Set

from typing import Union, Iterable, Optional, Set


class StreamingRemoveCodeBlockFilter2:
    """
    过滤LLM的流式输出，去除指定语言的代码块。
    支持：
    1. 流式分块 (Chunk) 处理
    2. 代码块嵌套 (通过反引号数量判断)
    3. 代码块缩进 (Indented code blocks)
    """

    def __init__(self, lang: Union[str, Iterable[str]] = "*"):
        self.target_langs: Optional[Set[str]] = None
        if lang == "*" or lang == "" or lang == [] or (isinstance(lang, list) and len(lang) == 0):
            self.target_langs = None
        elif isinstance(lang, str):
            self.target_langs = {lang.lower()}
        elif isinstance(lang, Iterable):
            self.target_langs = {l.lower() for l in lang}

        # --- 状态定义 ---
        # OUTSIDE_TEXT: 处于代码块外部，正在输出普通文本
        # OUTSIDE_INDENT: 代码块外部，行首，正在收集缩进空格
        # OUTSIDE_TICKS: 外部，行首缩进后，正在收集反引号
        # CAPTURING_LANG: 外部，反引号结束，正在读取语言标识
        # INSIDE_TEXT: 处于代码块内部 (可能是保留的，也可能是待移除的)
        # INSIDE_INDENT: 内部，行首，正在收集缩进
        # INSIDE_TICKS: 内部，行首缩进后，正在收集反引号判断是否闭合
        # INSIDE_TRAIL: 内部，闭合标记后，检查尾随空白

        self.state = "OUTSIDE_TEXT"

        # 缓冲区
        self.buffer = ""  # 通用缓冲区 (用于存反引号、语言名等)
        self.indent_buffer = ""  # 专门用于存行首缩进的空格

        self.active_delimiter_len = 0  # 当前代码块的围栏长度 (如 3 或 4)
        self.should_remove = False  # 当前代码块是否需要被移除

        # 初始状态视为行首，允许第一行就是缩进的代码块
        self.state = "OUTSIDE_INDENT"

    def _check_lang(self, lang: str) -> bool:
        if self.target_langs is None:
            return True
        return lang.lower() in self.target_langs

    def filter_chunk(self, chunk: str) -> str:
        output = []

        for char in chunk:
            processed = False
            while not processed:

                # ==================== 外部逻辑 ====================

                if self.state == "OUTSIDE_TEXT":
                    if char == '\n':
                        output.append(char)
                        self.state = "OUTSIDE_INDENT"
                        self.indent_buffer = ""
                        processed = True
                    elif char == '`':
                        # 这是一个极其罕见的情况：文本中间出现反引号。
                        # 标准 Markdown 代码块必须在行首（或缩进后）。
                        # 所以如果在 TEXT 状态遇到 `，它只能是行内代码，直接输出。
                        output.append(char)
                        processed = True
                    else:
                        output.append(char)
                        processed = True

                elif self.state == "OUTSIDE_INDENT":
                    if char in (' ', '\t'):
                        self.indent_buffer += char
                        processed = True
                    elif char == '`':
                        self.state = "OUTSIDE_TICKS"
                        self.buffer = "`"  # 开始收集反引号
                        processed = True
                    elif char == '\n':
                        # 空行（只有空格），直接输出
                        output.append(self.indent_buffer)
                        output.append(char)
                        self.indent_buffer = ""
                        # 状态不变，还是等待下一行的缩进
                        processed = True
                    else:
                        # 遇到非空非反引号，说明是普通缩进文本
                        output.append(self.indent_buffer)
                        output.append(char)
                        self.indent_buffer = ""
                        self.state = "OUTSIDE_TEXT"
                        processed = True

                elif self.state == "OUTSIDE_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        # 反引号收集结束，检查数量
                        if len(self.buffer) >= 3:
                            self.active_delimiter_len = len(self.buffer)
                            self.buffer = ""  # 清空buffer用于接下来的语言采集
                            self.state = "CAPTURING_LANG"
                            # 重新处理当前 char (它可能是换行符或语言名的一部分)
                        else:
                            # 这是一个伪代码块（反引号少于3个），作为普通文本输出
                            output.append(self.indent_buffer)  # 输出之前的缩进
                            output.append(self.buffer)  # 输出反引号
                            self.indent_buffer = ""
                            self.buffer = ""

                            if char == '\n':
                                output.append(char)
                                self.state = "OUTSIDE_INDENT"
                            else:
                                output.append(char)
                                self.state = "OUTSIDE_TEXT"
                            processed = True

                elif self.state == "CAPTURING_LANG":
                    if char == '\n':
                        lang = self.buffer.strip()
                        self.should_remove = self._check_lang(lang)

                        if not self.should_remove:
                            # 如果不移除，需要把之前缓存的缩进、反引号、语言名都吐出来
                            output.append(self.indent_buffer)
                            output.append("`" * self.active_delimiter_len)
                            output.append(self.buffer)
                            output.append(char)

                        self.buffer = ""
                        self.indent_buffer = ""  # 进入内部，清空缩进缓冲
                        self.state = "INSIDE_INDENT"  # 进入内部后，直接期待下一行
                        processed = True
                    else:
                        self.buffer += char
                        processed = True

                # ==================== 内部逻辑 ====================

                elif self.state == "INSIDE_TEXT":
                    # 在代码块内部，我们在等待换行符来检查下一行是否是闭合标记
                    if char == '\n':
                        if not self.should_remove:
                            output.append(char)
                        self.state = "INSIDE_INDENT"
                        self.indent_buffer = ""
                        processed = True
                    else:
                        if not self.should_remove:
                            output.append(char)
                        processed = True

                elif self.state == "INSIDE_INDENT":
                    if char in (' ', '\t'):
                        self.indent_buffer += char
                        processed = True
                    elif char == '`':
                        self.state = "INSIDE_TICKS"
                        self.buffer = "`"
                        processed = True
                    elif char == '\n':
                        # 代码块内的空行
                        if not self.should_remove:
                            output.append(self.indent_buffer)
                            output.append(char)
                        self.indent_buffer = ""
                        # 状态不变，继续找下一行
                        processed = True
                    else:
                        # 代码块内的普通缩进内容
                        if not self.should_remove:
                            output.append(self.indent_buffer)
                            output.append(char)
                        self.indent_buffer = ""
                        self.state = "INSIDE_TEXT"
                        processed = True

                elif self.state == "INSIDE_TICKS":
                    if char == '`':
                        self.buffer += "`"
                        processed = True
                    else:
                        # 检查是否是合法的闭合标记
                        # 1. 长度 >= 开始标记
                        # 2. 后面必须跟空白或换行 (这里通过 INSIDE_TRAIL 检查)
                        if len(self.buffer) >= self.active_delimiter_len:
                            if char == '\n':
                                # 完美闭合
                                if not self.should_remove:
                                    output.append(self.indent_buffer)
                                    output.append(self.buffer)
                                    output.append(char)

                                # 重置到外部状态
                                self.state = "OUTSIDE_INDENT"
                                self.active_delimiter_len = 0
                                self.should_remove = False
                                self.buffer = ""
                                self.indent_buffer = ""
                                processed = True
                            elif char in (' ', '\t'):
                                # 闭合标记后允许有空格
                                self.buffer += char  # 此时 buffer 混合了反引号和空格，稍后处理有点乱，不如切状态
                                # 修正：buffer只存反引号。用新状态处理尾随空格
                                self.state = "INSIDE_TRAIL"
                                processed = True  # 让 INSIDE_TRAIL 处理这个空格
                            else:
                                # 反引号后面跟了乱七八糟的字符，说明不是闭合标记
                                # 例如： ```sql
                                if not self.should_remove:
                                    output.append(self.indent_buffer)
                                    output.append(self.buffer)
                                    output.append(char)

                                self.buffer = ""
                                self.indent_buffer = ""
                                self.state = "INSIDE_TEXT"
                                processed = True
                        else:
                            # 反引号数量不够，不是闭合标记
                            if not self.should_remove:
                                output.append(self.indent_buffer)
                                output.append(self.buffer)
                                output.append(char)

                            self.buffer = ""
                            self.indent_buffer = ""
                            self.state = "INSIDE_TEXT"
                            processed = True

                elif self.state == "INSIDE_TRAIL":
                    if char == '\n':
                        # 闭合确认
                        if not self.should_remove:
                            output.append(self.indent_buffer)
                            output.append(self.buffer)  # 这里的 buffer 只有反引号
                            # 尾随的空格我们通常选择忽略或输出，这里直接输出当前换行
                            output.append(char)

                        self.state = "OUTSIDE_INDENT"
                        self.active_delimiter_len = 0
                        self.should_remove = False
                        self.buffer = ""
                        self.indent_buffer = ""
                        processed = True
                    elif char in (' ', '\t'):
                        # 忽略尾随空格，或者如果保留模式下，也可以选择输出
                        # 这里暂存如果不输出比较麻烦，简单起见：
                        # 如果最终不是闭合，这些空格要吐出来。
                        # 为了简化，假设闭合标记后全是空格接着换行就是合法。
                        self.buffer += char  # 把空格加到 buffer 里暂存
                        processed = True
                    else:
                        # 后面出现了非空字符，说明无效闭合
                        if not self.should_remove:
                            output.append(self.indent_buffer)
                            output.append(self.buffer)
                            output.append(char)

                        self.buffer = ""
                        self.indent_buffer = ""
                        self.state = "INSIDE_TEXT"
                        processed = True

        return "".join(output)

    def flush(self) -> str:
        """
        流结束时的清理。
        """
        output = []

        # 辅助函数：根据当前是否移除决定是否输出内容
        def safe_append(content):
            if not self.should_remove:
                output.append(content)

        # 处理残留状态
        if self.state == "OUTSIDE_INDENT":
            output.append(self.indent_buffer)

        elif self.state == "OUTSIDE_TICKS":
            output.append(self.indent_buffer)
            output.append(self.buffer)

        elif self.state == "CAPTURING_LANG":
            # 流断在语言定义处，视为未完成，把之前的头吐出来
            output.append(self.indent_buffer)
            output.append("`" * self.active_delimiter_len)
            output.append(self.buffer)

        elif self.state == "INSIDE_INDENT":
            safe_append(self.indent_buffer)

        elif self.state in ["INSIDE_TICKS", "INSIDE_TRAIL"]:
            # 流断在闭合标记处，说明没闭合
            safe_append(self.indent_buffer)
            safe_append(self.buffer)

        # 如果在 INSIDE_TEXT，内容已经实时输出了，不需要 flush

        # 重置
        self.buffer = ""
        self.indent_buffer = ""
        self.state = "OUTSIDE_INDENT"  # 默认为下一波流是新行
        self.should_remove = False

        return "".join(output)


import re
from typing import Union, Iterable, Set, Optional


class StreamingRemoveCodeBlockFilter3:
    def __init__(self, lang: Union[str, Iterable[str]] = "*"):
        """
        Args:
            lang:
                - "" or "*" or []: 提取所有代码块（任意语言）
                - "json": 仅提取 ```json 块
                - ["json", "yaml"]: 提取 json 或 yaml 块
        """
        # 初始化目标语言集合
        # self.target_langs is None 表示移除所有语言
        # self.target_langs is Set 表示只移除特定语言
        if not lang or lang == "*" or (isinstance(lang, list) and len(lang) == 0):
            self.target_langs = None
        elif isinstance(lang, str):
            self.target_langs = {lang.lower().strip()}
        else:
            self.target_langs = {l.lower().strip() for l in lang}

        # 缓冲区，用于处理流式输出中不完整的行
        self.buffer = ""

        # 状态标志
        self.in_block = False  # 是否在代码块内
        self.removing = False  # 当前代码块是否需要被移除
        self.block_indent = ""  # 当前代码块的缩进字符串（用于匹配结束符）

    def filter_chunk(self, chunk: str) -> str:
        """
        过滤单个 chunk
        Args:
            chunk: 流式输出的一个片段
        Returns:
            过滤后可以输出的内容
        """
        # 1. 将新 chunk 拼接到缓冲区
        text = self.buffer + chunk

        # 2. 按行分割，保留换行符 (keepends=True)
        #    流式处理核心：只有遇到换行符，我们才能确定这一行的完整含义（特别是判断代码块标记）
        lines = text.splitlines(keepends=True)

        # 3. 处理缓冲区残留
        #    如果最后一行没有换行符，说明该行可能未接收完，放回 buffer 等待下一个 chunk
        if text and not text.endswith('\n'):
            self.buffer = lines.pop()
        else:
            self.buffer = ""

        filtered_output = []

        # 4. 逐行处理状态机
        for line in lines:
            # 正则匹配：捕获缩进(group 1) 和 语言标记(group 2)
            # 匹配如: "  ```json \n", "```\n"
            match_start = re.match(r"^(\s*)```(.*?)\s*$", line)

            # --- 状态：在代码块内 ---
            if self.in_block:
                # 检查是否是结束标记
                # 结束标记必须是 ``` 开头，且缩进与开始标记一致（或更少，但在严格模式下通常匹配）
                # 这里我们逻辑是：如果遇到 ``` 且缩进匹配，则结束
                is_closing = False
                if match_start:
                    indent = match_start.group(1)
                    # 只有当缩进一致，且 ``` 后没有内容（或者只有空白）时，才视为结束
                    # 注意：Case 2 中嵌套的 ```md 是内容的一部分，不会触发这里的逻辑，
                    # 因为我们只在 in_block 状态下寻找结束符，且通常嵌套的缩进或语境不同。
                    # 简单处理：只要缩进一致且是 ``` 结尾，即视为退出。
                    content = match_start.group(2).strip()
                    if indent == self.block_indent and content == "":
                        is_closing = True

                if is_closing:
                    # 退出代码块状态
                    self.in_block = False
                    # 如果当前是在移除模式下，这一行（结束符）也不输出
                    if not self.removing:
                        filtered_output.append(line)
                    # 重置移除状态
                    self.removing = False
                else:
                    # 如果在代码块内，且不是结束符
                    if self.removing:
                        # 需要移除，跳过该行
                        continue
                    else:
                        # 不需要移除（非目标语言），保留该行
                        filtered_output.append(line)

            # --- 状态：在代码块外 ---
            else:
                # 检查是否是开始标记
                if match_start:
                    indent = match_start.group(1)
                    lang_tag = match_start.group(2).strip().lower()

                    # 判断是否需要移除该语言的代码块
                    should_remove = False
                    if self.target_langs is None:
                        should_remove = True  # 移除所有
                    elif lang_tag in self.target_langs:
                        should_remove = True  # 移除指定

                    # 进入代码块状态
                    self.in_block = True
                    self.block_indent = indent
                    self.removing = should_remove

                    # 如果不需要移除，这一行（开始符）需要保留
                    if not self.removing:
                        filtered_output.append(line)
                    # 如果需要移除，这一行被吞掉
                else:
                    # 普通文本，直接保留
                    filtered_output.append(line)

        return "".join(filtered_output)

    def flush(self) -> str:
        """
        (可选) 结束时调用，将缓冲区剩余内容输出。
        防止流结束时最后一行没有换行符导致内容丢失。
        """
        if self.buffer:
            remaining = self.buffer
            self.buffer = ""
            # 如果最后残留的内容还在移除状态的代码块里，则不输出
            if self.in_block and self.removing:
                return ""
            return remaining
        return ""

