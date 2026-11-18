#!/usr/bin/env python3
"""Synchronize editable Surge, Loon and Clash rule sets."""

import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ".rules-sync.json"
GROUPS = ("Direct", "Proxy")
DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP"}


class SyncError(ValueError):
    pass


def paths_for(group):
    return (
        f"surge/{group}.list",
        f"loon/{group}.list",
        f"clash/{group}.yaml",
        f"{group}.list",
    )


def normalize_rule(text, location):
    if not isinstance(text, str) or "\n" in text or "\r" in text:
        raise SyncError(f"{location}: 每条规则必须是单行字符串")
    parts = [part.strip() for part in text.split(",")]
    rule_type = parts[0].upper()
    if rule_type not in DOMAIN_TYPES | IP_TYPES:
        raise SyncError(f"{location}: 尚未支持跨三端同步的规则类型 {rule_type!r}")
    if len(parts) < 2 or not parts[1]:
        raise SyncError(f"{location}: 缺少规则匹配内容")
    value = parts[1]
    if re.search(r"\s", value):
        raise SyncError(f"{location}: 匹配内容不能包含空白字符")
    options = parts[2:]
    if options and (rule_type not in IP_TYPES or options != ["no-resolve"]):
        raise SyncError(f"{location}: 规则集不支持此策略或参数 {options!r}")
    if rule_type in {"DOMAIN", "DOMAIN-SUFFIX"}:
        value = value.encode("idna").decode("ascii").lower()
        labels = value.split(".")
        if len(value) > 253 or any(
            not re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label)
            for label in labels
        ):
            raise SyncError(f"{location}: 无效域名 {value!r}")
    if rule_type in {"IP-CIDR", "IP-CIDR6"}:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as error:
            raise SyncError(f"{location}: 无效 IP 网段 {value!r}") from error
        expected_version = 6 if rule_type == "IP-CIDR6" else 4
        if network.version != expected_version:
            raise SyncError(f"{location}: IP 网段与规则类型不匹配")
        value = str(network)
    if rule_type == "IP-ASN" and (not value.isdecimal() or not 0 <= int(value) <= 4294967295):
        raise SyncError(f"{location}: 无效 ASN {value!r}")
    if rule_type == "GEOIP":
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise SyncError(f"{location}: GEOIP 应使用两位地区代码")
    return ",".join([rule_type, value, *options])


def parse_document(path, content):
    lines = content.lstrip("\ufeff").splitlines()
    entries = []
    if path.endswith(".yaml"):
        try:
            root = yaml.compose("\n".join(lines), Loader=yaml.SafeLoader)
        except yaml.YAMLError as error:
            raise SyncError(f"{path}: YAML 解析失败\n{error}") from error
        if (
            not isinstance(root, yaml.MappingNode)
            or len(root.value) != 1
            or root.value[0][0].value != "payload"
            or not isinstance(root.value[0][1], yaml.SequenceNode)
        ):
            raise SyncError(f"{path}: 文件应只包含 payload 字符串列表，空列表写作 payload: []")
        cursor = 0
        for node in root.value[0][1].value:
            if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
                raise SyncError(f"{path}: payload 中每条规则必须是字符串")
            entries.extend(
                line.strip() for line in lines[cursor:node.start_mark.line]
                if not line.strip() or line.lstrip().startswith("#")
            )
            entries.append(normalize_rule(node.value, f"{path}:{node.start_mark.line + 1}"))
            cursor = node.end_mark.line + 1
        entries.extend(
            line.strip() for line in lines[cursor:]
            if not line.strip() or line.lstrip().startswith("#")
        )
    else:
        for number, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                entries.append(line)
            else:
                entries.append(normalize_rule(line, f"{path}:{number}"))
    while entries and not entries[-1]:
        entries.pop()
    return entries


def render_document(path, entries):
    if not path.endswith(".yaml"):
        return "\n".join(entries) + ("\n" if entries else "")
    if all(not entry or entry.startswith("#") for entry in entries):
        return "\n".join([*entries, "payload: []"]) + "\n"
    lines = ["payload:"]
    for entry in entries:
        if not entry or entry.startswith("#"):
            lines.append(entry)
        else:
            lines.append("  - " + json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def synchronize(root, bootstrap=False, check=False):
    state_path = root / STATE_FILE
    if bootstrap:
        if state_path.exists():
            raise SyncError("已经存在同步记录，请直接运行同步命令")
        previous = None
    else:
        if not state_path.exists():
            raise SyncError("缺少同步记录，首次初始化请使用 --bootstrap")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            previous = state["rules"]
            if state["version"] != 1 or set(previous) != set(GROUPS):
                raise ValueError("不兼容的同步记录")
            for group in GROUPS:
                if not isinstance(previous[group], list) or any(
                    not isinstance(entry, str) for entry in previous[group]
                ):
                    raise ValueError("规则记录应为字符串列表")
        except (ValueError, KeyError, TypeError) as error:
            raise SyncError(f"{STATE_FILE}: 无效的同步记录") from error

    # Validate both groups and every input before writing any output.
    outputs = {}
    selected = {}
    for group in GROUPS:
        paths = paths_for(group)
        if bootstrap:
            source = f"{group}.list"
            selected[group] = parse_document(source, (root / source).read_text(encoding="utf-8"))
        else:
            changed = {}
            for path in paths:
                if not (root / path).is_file():
                    raise SyncError(f"{path}: 文件缺失。清空名单请保留空文件，Clash 使用 payload: []")
                entries = parse_document(path, (root / path).read_text(encoding="utf-8"))
                if entries != previous[group]:
                    changed[path] = entries
            if changed and any(entries != next(iter(changed.values())) for entries in changed.values()):
                names = ", ".join(changed)
                raise SyncError(f"{group}: 多份文件的修改互相冲突，请统一名单后重试。涉及 {names}")
            selected[group] = next(iter(changed.values()), previous[group])
        for path in paths:
            outputs[path] = render_document(path, selected[group])

    outputs[STATE_FILE] = json.dumps(
        {"version": 1, "rules": selected}, ensure_ascii=False, indent=2
    ) + "\n"
    pending = {
        path: content for path, content in outputs.items()
        if not (root / path).exists() or (root / path).read_text(encoding="utf-8") != content
    }
    if check:
        return list(pending)
    for path, content in pending.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    return list(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="首次从根目录原始名单初始化")
    parser.add_argument("--check", action="store_true", help="只检查是否需要同步")
    args = parser.parse_args()
    try:
        changed = synchronize(ROOT, bootstrap=args.bootstrap, check=args.check)
    except (SyncError, OSError, UnicodeError) as error:
        print(f"同步失败 {error}", file=sys.stderr)
        return 1
    if changed:
        print("需要同步 " if args.check else "已同步 ", ", ".join(changed), sep="")
    else:
        print("所有规则已同步")
    return int(args.check and bool(changed))


if __name__ == "__main__":
    sys.exit(main())
