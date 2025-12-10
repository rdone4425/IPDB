import os
import requests
import time

# ------------------------- 配置区 -------------------------
# 从环境变量中获取 Cloudflare API Token，可以是单个或多个（逗号分割）
cf_tokens_str = os.getenv("CF_TOKENS", "").strip()
if not cf_tokens_str:
    raise Exception("环境变量 CF_TOKENS 未设置或为空")
api_tokens = [token.strip() for token in cf_tokens_str.split(",") if token.strip()]

# 子域名与对应的 IP 列表 URL 配置
# 如果只配置了 v4 则只处理 IPv4；如果同时配置了 v4 与 v6，则分别处理
subdomain_configs = {
    "bestcf": {
        "v4": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestCF/bestcfv4.txt",
        "v6": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestCF/bestcfv6.txt"
    },
    "bestproxy": {
        "v4": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestProxy/bestproxy.txt"
    },
    "bestcfv4": {
        "v4": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestCF/bestcfv4.txt"
    },
     "bestcfv6": {
        "v6": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestCF/bestcfv6.txt"
    },     
     "bestedg": {
        "v4": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestEDG/bestedgv4.txt"
    },
    # "bestgc": {
    #     "v4": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestGC/bestgcv4.txt",
    #     "v6": "https://raw.githubusercontent.com/ymyuuu/IPDB/refs/heads/main/BestGC/bestgcv6.txt"
    # },
}
# -----------------------------------------------------------

# 固定 DNS 记录类型映射，不作为配置项
dns_record_map = {
    "v4": "A",
    "v6": "AAAA"
}

# 获取指定 URL 的 IP 列表，仅返回前两行
def fetch_ip_list(url: str) -> list:
    response = requests.get(url)  # 发送 GET 请求获取数据
    response.raise_for_status()   # 检查响应状态，若请求失败则抛出异常
    ip_lines = response.text.strip().split('\n')
    # 清理每个 IP 地址，去除空格和空行
    cleaned_ips = [ip.strip() for ip in ip_lines if ip.strip()]
    return cleaned_ips[:2]  # 只返回前两行

# 获取 Cloudflare 第一个域区的信息，返回 (zone_id, domain)
def fetch_zone_info(api_token: str) -> tuple:
    headers = {
        "Authorization": f"Bearer {api_token}",  # API 认证
        "Content-Type": "application/json"
    }
    response = requests.get("https://api.cloudflare.com/client/v4/zones", headers=headers)
    response.raise_for_status()
    zones = response.json().get("result", [])
    if not zones:
        raise Exception("未找到域区信息")
    domain = zones[0]["name"]
    # 将域名转换为 Punycode（处理中文域名）
    try:
        domain = domain.encode('idna').decode('ascii')
    except Exception as e:
        print(f"域名编码警告: {e}, 使用原始域名: {domain}")
    return zones[0]["id"], domain

# 统一处理 DNS 记录操作
# operation 参数为 "delete" 或 "add"
def update_dns_record(api_token: str, zone_id: str, subdomain: str, domain: str, dns_type: str, operation: str, ip_list: list = None) -> None:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    # 拼接完整记录名称，subdomain 为 "@" 表示根域名
    full_record_name = domain if subdomain == "@" else f"{subdomain}.{domain}"
    
    if operation == "delete":
        # 获取所有匹配的记录
        query_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type={dns_type}&name={full_record_name}"
        response = requests.get(query_url, headers=headers)
        response.raise_for_status()
        records = response.json().get("result", [])

        if not records:
            print(f"未找到需要删除的 {subdomain} {dns_type} 记录 (查询: {full_record_name})")
            # 列出该子域名的所有记录类型，帮助调试
            all_query_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={full_record_name}"
            all_response = requests.get(all_query_url, headers=headers)
            if all_response.status_code == 200:
                all_records = all_response.json().get("result", [])
                if all_records:
                    print(f"  但找到以下其他类型的记录:")
                    for rec in all_records:
                        print(f"    - {rec['type']} {rec['name']} -> {rec['content']}")
            return

        # 删除所有匹配的记录
        delete_count = 0
        for record in records:
            try:
                delete_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record['id']}"
                del_resp = requests.delete(delete_url, headers=headers)
                del_resp.raise_for_status()
                print(f"✓ 删除 {subdomain} {dns_type} 记录: {record['content']} (ID: {record['id']})")
                delete_count += 1
                time.sleep(0.5)  # 添加延迟避免 API 限流
            except Exception as e:
                print(f"✗ 删除记录失败 {record['id']}: {str(e)}")

        print(f"共删除 {delete_count} 条 {subdomain} {dns_type} 记录")
    elif operation == "add" and ip_list is not None:
        # 针对每个 IP 地址添加新的 DNS 记录
        add_count = 0
        print(f"准备添加 {len(ip_list)} 条记录到 {full_record_name}")
        for ip in ip_list:
            payload = {
                "type": dns_type,
                "name": full_record_name,
                "content": ip,
                "ttl": 60,        # 使用 60 秒 TTL（当 proxied 为 False 时不能使用 1）
                "proxied": False  # 不启用 Cloudflare 代理
            }
            print(f"正在添加: {dns_type} {full_record_name} -> {ip}")
            try:
                response = requests.post(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
                                         json=payload, headers=headers)
                result = response.json()

                if response.status_code == 200 and result.get("success"):
                    print(f"✓ 添加 {subdomain} {dns_type} 记录: {ip}")
                    add_count += 1
                else:
                    # 打印详细的错误信息
                    errors = result.get("errors", [])
                    if errors:
                        for error in errors:
                            error_code = error.get("code", "unknown")
                            error_msg = error.get("message", "未知错误")
                            print(f"✗ 添加 {subdomain} {dns_type} 记录失败: {ip}")
                            print(f"  错误代码: {error_code}, 错误信息: {error_msg}")
                    else:
                        print(f"✗ 添加 {subdomain} {dns_type} 记录失败: {ip} - HTTP {response.status_code}")
                        print(f"  响应内容: {response.text}")

                time.sleep(0.5)  # 添加延迟避免 API 限流
            except Exception as e:
                print(f"✗ 添加 {subdomain} {dns_type} 记录异常: {ip} - {str(e)}")

        print(f"共添加 {add_count} 条 {subdomain} {dns_type} 记录")

def main():
    try:
        # 针对每个 API Token 进行处理，不直接输出 Token 信息，仅显示序号和域区信息
        for idx, token in enumerate(api_tokens, start=1):
            print("=" * 50)
            print(f"开始处理 API Token #{idx}")
            zone_id, domain = fetch_zone_info(token)
            print(f"域区 ID: {zone_id} | 域名: {domain}")

            # 遍历所有子域名配置
            for subdomain, version_urls in subdomain_configs.items():
                # 针对每个版本（如 v4、v6）分别处理
                for version_key, url in version_urls.items():
                    dns_type = dns_record_map.get(version_key)
                    if not dns_type:
                        continue
                    ips = fetch_ip_list(url)  # 获取 IP 列表(仅前两行)
                    print(f"\n处理 {subdomain} ({dns_type})...")
                    # 删除旧的 DNS 记录
                    update_dns_record(token, zone_id, subdomain, domain, dns_type, "delete")
                    # 等待一段时间确保删除操作完成
                    time.sleep(1)
                    # 添加新的 DNS 记录
                    if ips:
                        update_dns_record(token, zone_id, subdomain, domain, dns_type, "add", ips)
                    else:
                        print(f"{subdomain} ({dns_type}) 未获取到 IP")
            print(f"\n结束处理 API Token #{idx}")
            print("=" * 50 + "\n")
    except Exception as err:
        print(f"错误: {err}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
