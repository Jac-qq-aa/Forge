import sys

def main():
    input = lambda:sys.stdin.readline().strip()
    n = int(input())
    matrix = []
    for _ in range(n):
        arr= list(map(int,input().split()))
        matrix.append(arr)
    print(matrix)

if __name__ == "__main__":
    main()