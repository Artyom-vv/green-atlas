import { fireEvent, render, screen } from '@testing-library/react';
import { Plus } from 'lucide-react';
import { createRef, type ComponentPropsWithRef, type FC } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { LinkButton, LinkIconButton } from '../index';

interface NavigationLinkProps extends ComponentPropsWithRef<'a'> {
  to: string;
}
const NavigationLink: FC<NavigationLinkProps> = ({ to, ...props }) => (
  <a {...props} href={to} />
);

describe('navigation controls', () => {
  it('retains native anchor semantics and forwards routing composition/ref', () => {
    const ref = createRef<HTMLAnchorElement>(),
      onClick = vi.fn((event) => event.preventDefault());
    render(
      <LinkIconButton
        ref={ref}
        render={<NavigationLink to="/project" onClick={onClick} />}
        icon={<Plus />}
        label="Открыть проект"
        controlSize="compact"
      />,
    );
    const link = screen.getByRole('link', { name: 'Открыть проект' });
    expect(link).toHaveAttribute('href', '/project');
    expect(link).toHaveAttribute('data-size', 'compact');
    expect(link).not.toHaveAttribute('role', 'button');
    expect(ref.current).toBe(link);
    fireEvent.click(link, { ctrlKey: true });
    expect(onClick).toHaveBeenCalledOnce();
  });
  it('renders an ordinary native link without a custom renderer', () => {
    render(
      <LinkButton href="/catalog" startIcon={<Plus />}>
        Каталог
      </LinkButton>,
    );
    expect(screen.getByRole('link', { name: 'Каталог' })).toHaveAttribute(
      'href',
      '/catalog',
    );
  });
});
