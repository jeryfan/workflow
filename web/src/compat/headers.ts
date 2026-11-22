import Cookies from 'js-cookie'

export async function headers() {
  return new Headers()
}

export async function cookies() {
  const values = Cookies.get()
  return {
    get: (name: string) => {
      const value = values[name]
      return value === undefined ? undefined : { name, value }
    },
  }
}
