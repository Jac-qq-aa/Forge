"""上传图片到阿里云 OSS，用于数字人视频生成。

使用方法：
1. 把照片放到 /home/hugo/Forge/ 目录
2. 运行：python upload_image_to_oss.py 照片文件名
3. 脚本返回公网 URL
"""

import os
import sys
import oss2
from datetime import datetime

# OSS 配置（从 .env 读取）
OSS_BUCKET = os.getenv("OSS_BUCKET", "forge-digitalhuman")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "")


def upload_image(local_path: str) -> str:
    """上传图片到 OSS，返回公网 URL。"""

    if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        # 从 .env 文件读取
        env_file = "/home/hugo/Forge/.env"
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                for line in f:
                    if line.startswith("OSS_ACCESS_KEY_ID="):
                        OSS_ACCESS_KEY_ID = line.split("=")[1].strip()
                    elif line.startswith("OSS_ACCESS_KEY_SECRET="):
                        OSS_ACCESS_KEY_SECRET = line.split("=")[1].strip()

    if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        raise ValueError("OSS 凭证未配置，请检查 .env 文件")

    # 创建 OSS 客户端
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

    # 础保 Bucket 是公开读
    try:
        bucket.put_bucket_acl(oss2.BUCKET_ACL_PUBLIC_READ)
        print(f"[OSS] Bucket ACL 设置为 public-read")
    except Exception as e:
        print(f"[OSS] ACL 设置失败（可能已是 public-read）: {e}")

    # 生成 OSS 对象名称
    filename = os.path.basename(local_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    object_key = f"digital_human/avatar_{timestamp}_{filename}"

    # 上传文件
    print(f"[OSS] 上传: {local_path} → {object_key}")
    result = bucket.put_object_from_file(object_key, local_path)

    if result.status == 200:
        # 返回公网 URL
        url = f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{object_key}"
        print(f"[OSS] 上传成功！")
        print(f"[OSS] URL: {url}")
        return url
    else:
        raise Exception(f"上传失败: HTTP {result.status}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python upload_image_to_oss.py 照片文件名")
        print("示例: python upload_image_to_oss.py ruihe_front_desk.jpg")
        sys.exit(1)

    local_path = sys.argv[1]

    # 如果不是绝对路径，假设在 Forge 目录下
    if not os.path.isabs(local_path):
        local_path = f"/home/hugo/Forge/{local_path}"

    if not os.path.exists(local_path):
        print(f"文件不存在: {local_path}")
        sys.exit(1)

    url = upload_image(local_path)
    print(f"\n✅ 图片 URL: {url}")
    print(f"\n💡 将此 URL 设置为数字人图片:")
    print(f"   DEFAULT_IMAGE_URL = \"{url}\"")